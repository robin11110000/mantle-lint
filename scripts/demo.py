"""
Offline, one-command end-to-end demo for mantle-migrate-lint.

Tells the whole "catch -> fix -> prove" story with NO network and NO external
services: it starts an in-process OpenAI-compatible mock for the `--ai` step, so
any judge can reproduce the exact output with `python scripts/demo.py`.

Sections:
  1. Clean (Mantle-safe) contract  -> 0 findings, exit 0
  2. Vulnerable contract            -> deterministic findings, exit 1  (the core)
  3. + --ai                         -> exploitability ranking + reviewable patch
  4. + --benchmarks                 -> measured Mantle Sepolia numbers on MNT001
  5. CI gate                        -> exit codes for --fail-on

For a LIVE Tencent-hosted endpoint instead of the mock, don't run this; just set
MANTLE_LINT_AI_BASE_URL / MANTLE_LINT_AI_MODEL and pass --ai (see
docs/tencent-endpoint.md). The mock here exists only to keep the demo offline.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)  # so printed commands use clean relative paths

from mantle_lint import cli  # noqa: E402

VULN = "examples/VulnerableStaking.sol"
CLEAN = "examples/CleanStaking.sol"
RESULTS = "benchmarks/results.json"

# Rule-aware canned annotations for the offline mock (stands in for the model).
_PATCHES = {
    "MNT001": ("high",
               "value to a contract recipient can revert once the 2300-gas stipend buys less work under Mantle MNT/tokenRatio scaling",
               "--- a/VulnerableStaking.sol\n+++ b/VulnerableStaking.sol\n@@ unstake()\n-        payable(msg.sender).transfer(amount);\n+        (bool ok, ) = payable(msg.sender).call{value: amount}(\"\");\n+        require(ok, \"native transfer failed\");"),
    "MNT002": ("high",
               "the hardcoded 2300-gas budget can be too low under Mantle gas scaling and revert the call",
               "--- a/VulnerableStaking.sol\n+++ b/VulnerableStaking.sol\n@@ ping()\n-        (bool ok, ) = target.call{gas: 2300}(abi.encodeWithSignature(\"ping()\"));\n+        (bool ok, ) = target.call(abi.encodeWithSignature(\"ping()\"));"),
    "MNT004": ("medium",
               "BLOCKS_PER_DAY=7200 bakes in Ethereum's ~12s block time; rewards drift on Mantle's per-tx variable blocks",
               "--- a/VulnerableStaking.sol\n+++ b/VulnerableStaking.sol\n@@\n-    uint256 public constant BLOCKS_PER_DAY = 7200;\n+    uint256 public constant SECONDS_PER_DAY = 86400;"),
    "MNT005": ("high",
               "Mantle is chainId 5000/5003, so this mainnet-only guard rejects every Mantle transaction",
               "--- a/VulnerableStaking.sol\n+++ b/VulnerableStaking.sol\n@@\n-        require(block.chainid == 1, \"mainnet only\");\n+        require(block.chainid == 5000 || block.chainid == 5003, \"Mantle only\");"),
    "MNT006": ("high",
               "block.prevrandao is not secure entropy on the OP-Stack sequencer; derived randomness is predictable",
               "--- a/VulnerableStaking.sol\n+++ b/VulnerableStaking.sol\n@@ random()\n-        return uint256(keccak256(abi.encodePacked(block.prevrandao, blockhash(block.number - 1), seed)));\n+        return _vrf.requestRandomness(seed); // use a VRF/oracle"),
    "MNT007": ("high",
               "blockhash on an L2 only spans recent, sequencer-influenced blocks, so any randomness derived from it is predictable/manipulable",
               "--- a/VulnerableStaking.sol\n+++ b/VulnerableStaking.sol\n@@ drawWinner()\n-        return uint256(keccak256(abi.encodePacked(block.prevrandao, blockhash(block.number - 1), seed)));\n+        // blockhash on L2 only covers recent, sequencer-influenced blocks - unsafe as entropy\n+        return _vrf.requestRandomness(seed);"),
    "MNT008": ("medium",
               "reconstructing fees from tx.gasprice is wrong under Mantle's fee model (MNT gas, tokenRatio, L1-data + operator fees), so the refund over/under-charges",
               "--- a/VulnerableStaking.sol\n+++ b/VulnerableStaking.sol\n@@ adminSweep()\n-        uint256 fee = tx.gasprice * 21000;\n+        // Mantle's fee model differs from L1 EIP-1559; don't rebuild fees from tx.gasprice.\n+        // Use an explicit amount verified against Mantle's current fee behaviour.\n+        uint256 fee = fixedSweepFee;"),
    "MNT009": ("medium",
               "tx.origin auth is phishable via a malicious intermediary contract and also breaks account-abstraction / native meta-tx wallets on Mantle",
               "--- a/VulnerableStaking.sol\n+++ b/VulnerableStaking.sol\n@@ adminSweep()\n-        require(tx.origin == msg.sender, \"no contracts\");\n+        // tx.origin is phishable for auth and blocks smart-contract wallets on Mantle;\n+        // authorize on msg.sender / an explicit role instead.\n+        require(msg.sender == owner, \"not authorized\");"),
    "MNT010": ("low",
               "no direct exploit, but treating the native balance as ETH mislabels MNT and corrupts any USD/price math and event labels",
               "--- a/VulnerableStaking.sol\n+++ b/VulnerableStaking.sol\n@@ adminSweep()\n-        uint256 bal = address(this).balance - fee;\n+        // native balance is MNT on Mantle, not ETH - ensure price/USD math and labels use MNT\n+        uint256 bal = address(this).balance - fee;"),
    "MNT011": ("low",
               "the hardcoded mainnet WETH address points at nothing on Mantle; inject the Mantle address per network",
               "--- a/VulnerableStaking.sol\n+++ b/VulnerableStaking.sol\n@@\n-    address public constant WETH = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;\n+    address public immutable WETH; // set from the Mantle token list"),
}
_DEFAULT = ("medium", "re-verify this behaviour against current Mantle semantics before relying on it",
            "--- a/VulnerableStaking.sol\n+++ b/VulnerableStaking.sol\n@@\n- // review for Mantle\n+ // apply the Mantle-safe equivalent")


class _Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode("utf-8")
        rank, reason, patch = _DEFAULT
        for rid, val in _PATCHES.items():
            if rid in body:
                rank, reason, patch = val
                break
        content = json.dumps({"exploitability": rank, "reason": reason, "patch": patch})
        out = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def _banner(title: str) -> None:
    print("\n" + "=" * 74)
    print(f"  {title}")
    print("=" * 74)


def _run(argv, title: str) -> int:
    _banner(title)
    print(f"$ python -m mantle_lint.cli {' '.join(argv)}\n")
    rc = cli.run(argv)
    print(f"\n[exit code: {rc}]")
    return rc


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 0), _Mock)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cache = tempfile.mkdtemp()
    os.environ["MANTLE_LINT_AI_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    os.environ["MANTLE_LINT_AI_MODEL"] = "demo-mock"
    os.environ["MANTLE_LINT_AI_CACHE_DIR"] = cache

    print("mantle-migrate-lint demo  ::  catch -> fix -> prove  (fully offline)")
    _run([CLEAN, "--no-color"],
         "1) Clean, Mantle-safe contract -> 0 findings, exit 0")
    _run([VULN, "--no-color"],
         "2) CATCH: vulnerable contract -> deterministic Mantle findings, exit 1")
    _run([VULN, "--no-color", "--ai"],
         "3) FIX: --ai adds an exploitability ranking + reviewable patch (offline mock)")
    _run([VULN, "--no-color", "--benchmarks", RESULTS],
         "4) PROVE: --benchmarks attaches measured Mantle Sepolia numbers to MNT001")
    _run([VULN, "--no-color", "--fail-on", "MEDIUM"],
         "5) CI gate: --fail-on MEDIUM controls the non-zero exit")

    _banner("DONE - reproduced offline with one command")
    print("Live Tencent endpoint instead of the mock: set MANTLE_LINT_AI_BASE_URL /")
    print("MANTLE_LINT_AI_MODEL and pass --ai (see docs/tencent-endpoint.md).")
    srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())

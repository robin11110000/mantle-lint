"""
On-chain gas/behaviour benchmark for rule MNT001 on Mantle Sepolia (chainId 5003).

Deploys an L1-style payout vault (`.transfer()`) and a Mantle-safe vault
(`call{value:}`), then sends native MNT to a minimal recipient and to a "greedy"
recipient whose receive() exceeds the 2300-gas stipend. It records, from real
transaction receipts:

  * before -> greedy: the `.transfer()` payout REVERTS (the bug), and
  * after  -> greedy: the `call{value:}` payout SUCCEEDS (the fix), plus
  * the gas used by each successful payout (the measured delta).

Results are written to benchmarks/results.json with tx hashes + explorer links.

SECURITY: your private key is read ONLY from the PRIVATE_KEY environment variable
(never a CLI arg, never written to disk, never committed). Use a throwaway
testnet key. This script also never affects the linter, which stays zero-dependency.

Usage:
  # 1) preflight (no key needed): compile + connect + show balance
  python bench_mnt001.py --check --address 0xYourFundedAddress

  # 2) full run (needs a funded testnet key in the environment)
  #    PowerShell:  $env:PRIVATE_KEY="0x..."; python bench_mnt001.py
  #    bash:        PRIVATE_KEY=0x... python bench_mnt001.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

try:
    import solcx
    from web3 import Web3
except ImportError:
    sys.stderr.write(
        "Missing deps. Create a venv and `pip install -r benchmarks/requirements.txt`.\n"
    )
    raise

HERE = os.path.dirname(os.path.abspath(__file__))
SOL_PATH = os.path.join(HERE, "contracts", "Benchmark.sol")
RESULTS_PATH = os.path.join(HERE, "results.json")

SOLC_VERSION = "0.8.23"
CHAIN_ID = 5003
DEFAULT_RPC = "https://rpc.sepolia.mantle.xyz"
EXPLORER = "https://sepolia.mantlescan.xyz"
FUND_WEI = 10 ** 15  # 0.001 MNT per payout scenario


def compile_contracts():
    with open(SOL_PATH, "r", encoding="utf-8") as fh:
        source = fh.read()
    solcx.install_solc(SOLC_VERSION)
    solcx.set_solc_version(SOLC_VERSION)
    out = solcx.compile_standard(
        {
            "language": "Solidity",
            "sources": {"Benchmark.sol": {"content": source}},
            "settings": {
                "optimizer": {"enabled": True, "runs": 200},
                "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
            },
        }
    )
    units = out["contracts"]["Benchmark.sol"]
    return {
        name: {"abi": c["abi"], "bytecode": c["evm"]["bytecode"]["object"]}
        for name, c in units.items()
    }


def _raw(signed):
    # web3 v6 -> raw_transaction ; v5 -> rawTransaction
    return getattr(signed, "raw_transaction", None) or signed.rawTransaction


class Runner:
    def __init__(self, w3, acct):
        self.w3 = w3
        self.acct = acct
        self.nonce = w3.eth.get_transaction_count(acct.address)
        self.gas_price = w3.eth.gas_price

    def _next_nonce(self):
        n = self.nonce
        self.nonce += 1
        return n

    def _send(self, tx, expect_revert=False):
        signed = self.acct.sign_transaction(tx)
        h = self.w3.eth.send_raw_transaction(_raw(signed))
        rcpt = self.w3.eth.wait_for_transaction_receipt(h, timeout=300)
        status = rcpt["status"]
        if expect_revert and status != 0:
            raise RuntimeError("expected a revert but the tx succeeded")
        if not expect_revert and status != 1:
            raise RuntimeError(f"tx reverted unexpectedly: {h.hex()}")
        return rcpt

    def deploy(self, spec, *args):
        c = self.w3.eth.contract(abi=spec["abi"], bytecode=spec["bytecode"])
        tx = c.constructor(*args).build_transaction({
            "from": self.acct.address, "nonce": self._next_nonce(),
            "chainId": CHAIN_ID, "gasPrice": self.gas_price,
        })
        rcpt = self._send(tx)
        return self.w3.eth.contract(address=rcpt["contractAddress"], abi=spec["abi"])

    def call_fn(self, func, value=0, gas=None, expect_revert=False):
        params = {
            "from": self.acct.address, "nonce": self._next_nonce(),
            "chainId": CHAIN_ID, "gasPrice": self.gas_price, "value": value,
        }
        if gas is not None:
            params["gas"] = gas  # explicit gas so a known-reverting tx still mines
        tx = func.build_transaction(params)
        return self._send(tx, expect_revert=expect_revert)


def run_full(w3):
    pk = os.environ.get("PRIVATE_KEY")
    if not pk:
        sys.stderr.write("PRIVATE_KEY env var is required for the full run.\n")
        return 2
    acct = w3.eth.account.from_key(pk)
    bal = w3.eth.get_balance(acct.address)
    print(f"account {acct.address}  balance {Web3.from_wei(bal, 'ether')} MNT")
    if bal == 0:
        sys.stderr.write("Account has 0 MNT — fund it from the Mantle Sepolia faucet.\n")
        return 2

    specs = compile_contracts()
    r = Runner(w3, acct)

    print("deploying recipients + vaults ...")
    minimal = r.deploy(specs["MinimalReceiver"])
    greedy = r.deploy(specs["GreedyReceiver"])

    scenarios = [
        ("before_to_minimal", "VaultBefore", minimal.address, False, None),
        ("before_to_greedy", "VaultBefore", greedy.address, True, 300000),
        ("after_to_minimal", "VaultAfter", minimal.address, False, None),
        ("after_to_greedy", "VaultAfter", greedy.address, False, None),
    ]

    contracts = {"MinimalReceiver": minimal.address, "GreedyReceiver": greedy.address}
    results = []
    for name, vault_name, recipient, expect_revert, gas in scenarios:
        vault = r.deploy(specs[vault_name])
        contracts[f"{vault_name}:{name}"] = vault.address
        r.call_fn(vault.functions.fund(), value=FUND_WEI)  # fund the vault
        print(f"  scenario {name} (expect_revert={expect_revert}) ...")
        rcpt = r.call_fn(vault.functions.payout(recipient), gas=gas,
                         expect_revert=expect_revert)
        txh = rcpt["transactionHash"].hex()
        results.append({
            "name": name, "vault": vault_name, "recipient": recipient,
            "expectRevert": expect_revert, "txHash": txh,
            "status": rcpt["status"], "gasUsed": rcpt["gasUsed"],
            "block": rcpt["blockNumber"],
            "explorer": f"{EXPLORER}/tx/{txh}",
        })
        print(f"    status={rcpt['status']} gasUsed={rcpt['gasUsed']}")

    by = {s["name"]: s for s in results}
    g_before = by["before_to_minimal"]["gasUsed"]
    g_after = by["after_to_minimal"]["gasUsed"]
    summary = {
        "before_to_greedy_reverted": by["before_to_greedy"]["status"] == 0,
        "after_to_greedy_succeeded": by["after_to_greedy"]["status"] == 1,
        "gas_minimal_before": g_before,
        "gas_minimal_after": g_after,
        "gas_delta_after_minus_before": g_after - g_before,
        "note": ("gasUsed is L2 execution gas; Mantle total fee also includes an "
                 "L1-data-fee and operator-fee component not captured here."),
    }

    payload = {
        "rule": "MNT001",
        "measuredAtUTC": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "network": {"rpc": w3.provider.endpoint_uri, "chainId": CHAIN_ID,
                    "explorer": EXPLORER},
        "compiler": SOLC_VERSION,
        "optimizer": {"enabled": True, "runs": 200},
        "account": acct.address,
        "contracts": contracts,
        "scenarios": results,
        "summary": summary,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nwrote {RESULTS_PATH}")
    print(json.dumps(summary, indent=2))
    return 0


def run_check(w3, address):
    print(f"RPC {w3.provider.endpoint_uri}  connected={w3.is_connected()}")
    print(f"chainId {w3.eth.chain_id} (expected {CHAIN_ID})")
    print(f"gasPrice {w3.eth.gas_price}")
    print("compiling contracts ...")
    specs = compile_contracts()
    print(f"compiled OK: {', '.join(sorted(specs))}")
    addr = address or os.environ.get("MANTLE_BENCH_ADDRESS")
    if not addr and os.environ.get("PRIVATE_KEY"):
        addr = w3.eth.account.from_key(os.environ["PRIVATE_KEY"]).address
    if addr:
        bal = w3.eth.get_balance(Web3.to_checksum_address(addr))
        print(f"balance of {addr}: {Web3.from_wei(bal, 'ether')} MNT")
    else:
        print("(pass --address to also show a balance)")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="MNT001 on-chain benchmark (Mantle Sepolia).")
    p.add_argument("--check", action="store_true",
                   help="Preflight: compile + connect + show balance (no key needed).")
    p.add_argument("--address", help="Address to show balance for in --check mode.")
    p.add_argument("--rpc", default=os.environ.get("MANTLE_SEPOLIA_RPC", DEFAULT_RPC))
    args = p.parse_args(argv)

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        sys.stderr.write(f"Cannot connect to RPC: {args.rpc}\n")
        return 2
    if args.check:
        return run_check(w3, args.address)
    return run_full(w3)


if __name__ == "__main__":
    sys.exit(main())

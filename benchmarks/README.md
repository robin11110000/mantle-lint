# On-chain gas benchmarks (Mantle Sepolia)

Turns the gas-related rule recommendations into **measured numbers from real
Mantle Sepolia (chainId 5003)** transactions. Currently covers **MNT001**
(`.transfer()` / `.send()` 2300-gas stipend → `call{value:}`).

> **This is a dev-only harness.** It needs `web3.py` + `py-solc-x` and is fully
> isolated here. The linter itself (`mantle_lint`) stays **zero-dependency** — it
> never imports anything in this folder.

## What it proves

It deploys an L1-style vault (`VaultBefore`, pays out with `.transfer()`) and a
Mantle-safe vault (`VaultAfter`, pays out with `call{value:}` + success check),
then pays native MNT to two recipients:

- `MinimalReceiver` — empty `receive()`, fits in the 2300-gas stipend.
- `GreedyReceiver` — `receive()` does an SSTORE (>2300 gas), modelling a contract
  wallet / proxy / accounting hook.

From real receipts it records: **`before → greedy` reverts (the bug)**,
**`after → greedy` succeeds (the fix)**, and the **gas used** by each successful
payout.

## Setup

```bash
# from the repo's mantle-migrate-lint/ dir
python -m venv benchmarks/.venv
# Windows PowerShell:
benchmarks\.venv\Scripts\Activate.ps1
# bash/macOS/Linux:
source benchmarks/.venv/bin/activate

pip install -r benchmarks/requirements.txt
```

## 1) Preflight (no private key needed)

Compiles the contracts and checks RPC connectivity + your balance:

```bash
python benchmarks/bench_mnt001.py --check --address 0xYourFundedAddress
```

## 2) Full run (needs a funded testnet key)

> **Security:** the key is read only from the `PRIVATE_KEY` env var — never a CLI
> arg, never written to disk, never committed. Use a **throwaway testnet key**.

```bash
# PowerShell
$env:PRIVATE_KEY="0x..."; python benchmarks/bench_mnt001.py

# bash
PRIVATE_KEY=0x... python benchmarks/bench_mnt001.py
```

The RPC defaults to `https://rpc.sepolia.mantle.xyz` (override with
`MANTLE_SEPOLIA_RPC`). Results are written to `benchmarks/results.json` with tx
hashes and explorer links — commit that file as the verifiable record.

## Honest caveats

- The headline result is partly **behavioural** (transfer-to-contract reverts),
  not only a gas delta — both are reported.
- `gasUsed` is **L2 execution gas**. Mantle's *total* fee also includes an
  L1-data-fee and operator-fee component that this harness does not measure.
  Verify against https://docs.mantle.xyz before quoting a full fee comparison.

# Demo — catch → fix → prove

**On every contract, catch the L1 assumptions that break on Mantle, draft the fix, and prove the gas impact with measured on-chain numbers.**

`mantle-migrate-lint` is one CI-native Mantle DevTool with two pillars on a zero-dependency core:

- **Catch + Fix (core engines):** a Mantle-specific linter (11 rules) plus an optional `--ai` triage layer that ranks exploitability and drafts a reviewable patch per finding.
- **Prove (second pillar):** real Mantle Sepolia measurements attached to gas-related findings via `--benchmarks`.

> Windows uses `python`; Linux/macOS docs use `python3`.

---

## Run the whole story offline, one command

```bash
python scripts/demo.py
```

No network, no API keys, no Tencent account: the script starts an in-process OpenAI-compatible mock for the `--ai` step so the output is identical for anyone. It walks all five sections below.

---

## Or step through it manually

### 1. Clean, Mantle-safe contract → silent

```bash
python -m mantle_lint.cli examples/CleanStaking.sol
```
→ `No Mantle migration issues found.` · **exit 0**. Proves the rules target real divergences, not style.

### 2. CATCH — vulnerable contract → deterministic findings

```bash
python -m mantle_lint.cli examples/VulnerableStaking.sol
```
→ **13 findings** (7 HIGH / 4 MEDIUM / 2 INFO), each with the rule, exact line, *why it breaks on Mantle*, and a fix · **exit 1**.

### 3. FIX — add AI triage

```bash
# offline demo uses a local mock (scripts/demo.py wires it automatically).
# live: set the env vars below, then pass --ai (see docs/tencent-endpoint.md)
export MANTLE_LINT_AI_BASE_URL=http://localhost:11434/v1
export MANTLE_LINT_AI_MODEL=qwen2.5-coder:1.5b
python -m mantle_lint.cli examples/VulnerableStaking.sol --ai
```
→ each deterministic finding gains an **`AI exploitability:`** ranking + reason and a **reviewable unified-diff patch** scoped to this contract. The rules stay ground truth — the AI only annotates, never invents findings. Off-schema model output is skipped gracefully. **Switching from the offline mock to a live Tencent endpoint is just these two env vars.**

### 4. PROVE — attach measured on-chain numbers

```bash
python -m mantle_lint.cli examples/VulnerableStaking.sol --benchmarks benchmarks/results.json
```
→ MNT001 findings gain a **`measured:`** line from real Mantle Sepolia (chainId 5003) transactions: `.transfer()` to a contract recipient **reverted**; `call{value:}` **succeeded**; minimal-recipient gasUsed 31118 vs 31145 (+27). So the fix is about **correctness, not gas** — and we say so. Tx hashes + explorer links live in [`benchmarks/results.json`](benchmarks/results.json).

### 5. CI gate

```bash
python -m mantle_lint.cli examples/VulnerableStaking.sol --fail-on MEDIUM
```
→ non-zero exit gates the PR. `.github/workflows/mantle-lint.yml` also uploads SARIF for inline PR annotations.

### 6. PROVE in CI — the gas-regression bot

The whole loop lives on the PR. `.github/workflows/gas-regression.yml` re-runs the on-chain harness on PRs that touch the benchmark contracts, diffs measured Mantle Sepolia gas against the committed baseline, and posts a sticky comment. You can render that exact comment locally:

```bash
python benchmarks/gas_regression.py        # prints the PR-comment Markdown
```

→
```markdown
## ⛽ Mantle gas report — MNT001
| Scenario | status | gasUsed | baseline | Δ |
|---|---|---|---|---|
| `before_to_greedy` | revert | 33385 | 33385 | +0 |
| `after_to_greedy`  | ok     | 53369 | 53369 | +0 |
- ✅ `.transfer()` to a contract recipient reverts on-chain (the bug).
- ✅ `call{value:}` succeeds (the fix).
> ✅ No gas regression vs the committed baseline.
```

**Safety:** throwaway testnet key as a GitHub secret; `pull_request` (not `pull_request_target`), so fork PRs skip the on-chain run gracefully — untrusted code never runs with the key.

---

## What stays true

- **Zero-dependency core.** `--ai` and `--benchmarks` are opt-in; with both off, behavior, output, and the no-network guarantee are identical to the plain linter.
- **Clean contract stays at 0 findings**; every rule keeps a test (`python tests/test_rules.py`).

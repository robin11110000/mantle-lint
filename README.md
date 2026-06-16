# mantle-migrate-lint

**Catch the Ethereum-L1 assumptions that break on Mantle, draft the fix, and prove the gas impact with measured on-chain numbers.**

Most contracts "deploy fine" on Mantle because it's EVM-compatible — and then misbehave in production, because a handful of L1 assumptions are no longer true. `mantle-migrate-lint` is one CI-native Mantle DevTool that flags those exact assumptions, explains *why* each breaks on Mantle, drafts a concrete fix, and backs gas-related findings with measured on-chain numbers. It runs in your terminal or as a CI gate, with zero runtime dependencies (just Python 3.8+).

It is **Mantle-specific by design** — not a generic Solidity linter. Every rule maps to a documented divergence between Mantle and Ethereum mainnet.

**One tool, three pillars on a zero-dependency core:**

- **Catch** — the Mantle-specific linter (11 rules), with a positive case + clean fixture proving each one. *(core engine)*
- **Fix** — an optional `--ai` triage layer that ranks exploitability and drafts a reviewable patch per finding, via a self-hosted, OpenAI-compatible endpoint. The rules stay ground truth; the AI only annotates. *(core engine)*
- **Prove** — a gas-regression CI bot that measures real Mantle Sepolia gas for the **MNT001 reference scenarios** on PRs and comments the deltas, reusing the benchmark harness. *(makes the value legible in CI)*

---

## Why this exists

"Full EVM compatibility" is the selling point and the trap. A contract that compiles and deploys can still be wrong on Mantle because:

- **MNT, not ETH, is the native gas token.** Gas is rescaled from ETH-gas via a `tokenRatio` (price(ETH) / price(MNT)). Fixed-gas-stipend transfers (`.transfer()` / `.send()`) and hardcoded `{gas: N}` budgets that were safe on L1 can revert or misbehave. *(Source: OpenZeppelin "Mantle OP-Geth Audit".)*
- **Block production is per-transaction with variable timing.** Mantle generates a block per transaction, so `block.number` is **not** a ~12-second clock. Any "blocks as time" logic (vesting, reward-per-block, block-denominated deadlines) drifts. *(Source: Mantle docs / explorer education.)*
- **Mantle V2 is built on the OP Stack** (an op-geth fork), inheriting Optimism's L2 semantics for block-level opcodes and randomness, with a centralized sequencer.
- **Chain IDs differ:** Mantle mainnet is `5000`, Mantle Sepolia testnet is `5003`. Hardcoded `chainid == 1` branches never run.

These are precisely the bugs that don't show up in a quick testnet click-through but cost you in production.

---

## Install & run

No dependencies to install. Clone and run:

> **Windows note:** commands below use `python3` (Linux/macOS). On Windows, use `python`.

```bash
# scan a single file
python3 -m mantle_lint.cli examples/VulnerableStaking.sol

# scan a whole contracts directory
python3 -m mantle_lint.cli ./contracts

# machine-readable output
python3 -m mantle_lint.cli ./contracts --format json
python3 -m mantle_lint.cli ./contracts --format sarif > mantle-lint.sarif

# control the CI gate (default fails on HIGH)
python3 -m mantle_lint.cli ./contracts --fail-on MEDIUM
```

Optional install as a command:

```bash
pip install -e .
mantle-migrate-lint ./contracts
```

### Example

Running against the included `examples/VulnerableStaking.sol` reports 13 issues (7 HIGH / 4 MEDIUM / 2 INFO) and exits non-zero; the Mantle-safe `examples/CleanStaking.sol` reports **zero** — demonstrating the rules target real divergences, not style.

---

## Rule catalog

| ID | Severity | What it catches | Why it breaks on Mantle |
|----|----------|-----------------|--------------------------|
| MNT001 | HIGH | `.transfer()` / `.send()` native value moves | 2300-gas stipend assumes L1 opcode costs; MNT gas + tokenRatio can make it revert |
| MNT002 | HIGH | Hardcoded `{gas: N}` in external calls | Fixed gas budget assumes L1 costs |
| MNT003 | MEDIUM | `block.number` in multiplicative time math | Variable per-tx block time — not a clock |
| MNT004 | HIGH | `BLOCKS_PER_*` / `=7200` / `*12 seconds` constants | Bakes in Ethereum's ~12s block time |
| MNT005 | HIGH | `chainid == 1` mainnet checks | Mantle is 5000 / 5003 |
| MNT006 | MEDIUM | `block.prevrandao` / `block.difficulty` | Not meaningful/secure on the L2 sequencer |
| MNT007 | MEDIUM | `blockhash()` | Recent-block only, sequencer-influenced |
| MNT008 | MEDIUM | `tx.gasprice` / `block.basefee` logic | Mantle fee model differs from L1 EIP-1559 |
| MNT009 | MEDIUM | `tx.origin` authorization | Unsafe generally; native meta-tx compounds it |
| MNT010 | INFO | `address(this).balance` accounting | Native balance is MNT, not ETH |
| MNT011 | INFO | Hardcoded L1 token addresses (WETH/USDC/USDT) | Different addresses on Mantle |

The scanner is comment- and string-aware, so it never matches inside a `//` comment, `/* */` block, or a string literal, and reported line numbers are exact.

---

## AI triage layer (optional, `--ai`)

The deterministic rules above are the **ground truth**. With the optional `--ai` flag, each *already-confirmed* finding is sent — with its surrounding code — to a **self-hosted, OpenAI-compatible endpoint** (e.g. vLLM or Ollama on Tencent Cloud HAI/CVM) and annotated with:

- an **exploitability ranking** (`low` / `medium` / `high`) and a one-line reason for *this* contract, and
- a **reviewable unified-diff patch suggestion** scoped to this finding.

The AI **never invents findings** — it only annotates the deterministic ones. Patches are **suggestions for human review, never auto-applied** (see *Honest limitations*). Output is reproducible (temperature 0 + an on-disk response cache), and the layer **degrades gracefully**: if the endpoint is down it prints a warning and falls back to deterministic-only output rather than crashing.

```bash
export MANTLE_LINT_AI_BASE_URL=http://<your-tencent-host>:8000/v1   # required with --ai
export MANTLE_LINT_AI_MODEL=<model-id-your-endpoint-serves>          # required with --ai
export MANTLE_LINT_AI_API_KEY=<token>                                # optional (self-hosted may not need one)
# optional: MANTLE_LINT_AI_TIMEOUT (seconds, default 30), MANTLE_LINT_AI_CACHE_DIR

python3 -m mantle_lint.cli examples/VulnerableStaking.sol --ai
```

**The default (no `--ai`) is unchanged: zero runtime dependencies, no network, byte-identical output.** The AI layer is stdlib-only (`urllib`/`json`/`hashlib`/`os`) and is only imported when `--ai` is passed.

**Standing up the endpoint:** see [`docs/tencent-endpoint.md`](docs/tencent-endpoint.md) for a step-by-step runbook (Ollama on a CPU-only Tencent Cloud instance, served behind an SSH tunnel). Then verify it end-to-end with the stdlib health check:

```bash
python3 scripts/ai_smoke.py   # checks: config -> endpoint reachable -> finding round-trips
```

---

## CI integration

The whole **catch → fix → prove** loop lives on the PR:

**1. Catch (gate).** `.github/workflows/mantle-lint.yml` runs the linter on every PR, uploads **SARIF** so findings appear inline in the "Files changed" view, and fails the check on HIGH-severity issues — turning "did anyone remember the Mantle gotchas?" into an automatic gate.

**2. Prove (gas bot).** `.github/workflows/gas-regression.yml` is the second pillar: on PRs that touch the benchmark contracts/harness, it runs the harness against **Mantle Sepolia**, diffs the measured gas against the committed baseline (`benchmarks/gas-snapshot.json`), and posts a sticky PR comment like:

```markdown
## ⛽ Mantle gas report — MNT001
Measured on Mantle Sepolia (chainId 5003). Δ is vs the committed baseline.

| Scenario | status | gasUsed | baseline | Δ |
|---|---|---|---|---|
| `before_to_minimal` | ok | 31118 | 31118 | +0 |
| `before_to_greedy` | revert | 33385 | 33385 | +0 |
| `after_to_minimal` | ok | 31145 | 31145 | +0 |
| `after_to_greedy` | ok | 53369 | 53369 | +0 |

- ✅ `.transfer()` to a contract recipient **reverts** on-chain (the L1 assumption that breaks on Mantle).
- ✅ `call{value:}` **succeeds** (the fix).
> ✅ No gas regression vs the committed baseline.
```

**Safety model:** the testnet key is a GitHub secret (a throwaway key); the workflow uses `pull_request` (**not** `pull_request_target`), so fork PRs get no secret and **skip the on-chain run gracefully** — untrusted code never runs with the key. The bot comments; it doesn't fail the build (correctness is the linter's job).

**Scope (honest):** today the bot benchmarks the committed **MNT001 reference scenarios** (the `Vault*` + receiver fixtures in `benchmarks/contracts/`) and diffs them against the baseline — it does **not** yet auto-detect and benchmark arbitrary contracts a PR changes. It's a gas-regression guard on those reference scenarios and the foundation for generic changed-contract benchmarking, not a drop-in "measure whatever changed" bot.

---

## Reproducible verification

```bash
python3 tests/test_rules.py      # or: python3 -m pytest -q
```

Each rule has a positive case it must catch and the clean contract must stay silent; the ERC-20 `transfer(to, amount)` case asserts the tool does **not** false-positive on token transfers.

---

## On-chain benchmarks (`--benchmarks`)

For gas-related rules, recommendations are backed by **real Mantle Sepolia (chainId 5003) measurements** — see [`benchmarks/`](benchmarks/). The harness deploys an L1-style `.transfer()` payout vault and a `call{value:}` vault and exercises both.

**MNT001, measured on-chain:**

| Scenario | Result |
|---|---|
| `.transfer()` → contract recipient needing >2300 gas | **REVERTS** (status 0) — the bug |
| `call{value:}` → same recipient | **Succeeds** — the fix |
| gasUsed to a minimal recipient | transfer `31118` vs call `31145` (**+27**) |

So the fix is about **correctness** (not reverting on contract recipients), not gas savings — and we report it that way. *(gasUsed is L2 execution gas; Mantle's total fee also has L1-data-fee + operator-fee components not measured here.)* Full tx hashes + explorer links are in [`benchmarks/results.json`](benchmarks/results.json).

Attach these numbers to findings:

```bash
python3 -m mantle_lint.cli examples/VulnerableStaking.sol --benchmarks benchmarks/results.json
```

Off by default and stdlib-only, so the core tool's zero-dependency guarantee is unaffected.

---

## How this maps to the AI Awakening — AI DevTools (Tencent Cloud) scorecard

| Part B row (pts) | How this tool scores it |
|---|---|
| Optimization / audit output quality (13) | Code-level, Mantle-specific findings with concrete fixes — not generic LLM commentary |
| Developer productivity impact (10) | Drops into CLI + GitHub PR/CI with SARIF inline annotations, exit-code gating, **and a gas-regression bot that comments measured Mantle gas (MNT001 reference scenarios) on PRs** |
| Verifiability & benchmarking (10) | Deterministic rules + a reproducible test suite; clean vs. vulnerable fixtures prove signal; **real on-chain Mantle Sepolia measurements** for MNT001 (`--benchmarks`), reported on PRs by the gas-regression bot |
| Execution & demo (5) | Runs end-to-end out of the box; **one-command offline demo** (`python scripts/demo.py`, see [DEMO.md](DEMO.md)); reproducible from this README |
| Tencent Cloud + Mantle integration depth (12) | AI triage layer (`--ai`) annotates each deterministic finding with an exploitability ranking + a reviewable patch, with inference on a **self-hosted, OpenAI-compatible endpoint on Tencent Cloud**. Engine + flag implemented; runbook to go live in [`docs/tencent-endpoint.md`](docs/tencent-endpoint.md), with a `scripts/ai_smoke.py` health check. |

The deterministic engine + the `--ai` layer are in place and there's a runbook + smoke test for the Tencent Cloud endpoint; running that runbook makes the integration live end-to-end.

---

## Roadmap to a full submission (what to add for the win)

1. ✅ **AI explanation/triage layer (`--ai`).** Implemented: for each deterministic finding it generates a context-aware patch diff and ranks exploitability, with the rules kept as ground truth so the AI augments rather than hallucinates. *Remaining:* provision the self-hosted, OpenAI-compatible inference endpoint on Tencent Cloud (HAI/CVM) to claim the 12-pt integration row end-to-end.
2. ✅ **Real benchmarking.** Implemented for MNT001: a harness deploys before/after contracts to Mantle Sepolia and records real receipts (`benchmarks/`), and `--benchmarks` attaches them to findings. The measured headline is *behavioural* — `.transfer()` to a contract recipient reverts; `call{value:}` succeeds. *Remaining:* extend the harness to MNT002 and the other gas rules.
3. **Generalize the gas bot to changed contracts (the true "bundle-size bot, but for gas").** Today the gas-regression CI bot benchmarks the committed **MNT001 reference scenarios** and diffs them against the baseline — it does not yet auto-detect and benchmark whatever contracts a PR changes. Closing that gap is deliberate next work, not a hidden gap: runtime gas is path-dependent (it needs to know *how* to call a function), so the tractable first cut is **deployment gas / bytecode size of each changed contract** — a faithful bundle-size analog that needs no invocation — with an optional `benchmark()`-entrypoint (or Foundry gas-snapshot) convention for true runtime gas where a contract provides one.
4. **AST upgrade (production hardening).** Swap the lexical matcher for a real Solidity AST (`@solidity-parser/parser` or the solc AST). The rule structure is already isolated for this.
5. **Auto-fix, conservatively.** Some rules (e.g. `transfer` → `call{value:}`) can be auto-rewritten, but blind Solidity rewriting is unsafe (reentrancy ordering), so any auto-fix should emit a reviewable diff, never silently mutate code.

## Honest limitations

- This MVP uses a lexical scanner, not a full AST, so it favors precision on the documented patterns over exhaustive dataflow coverage.
- The `--ai` triage layer is fully implemented and validated against a local OpenAI-compatible mock, with a runbook + smoke test for a self-hosted Tencent Cloud endpoint — but it has **not yet been run against a live Tencent-hosted endpoint**. It is ready to deploy; treat it as "integration-ready", not "integration-proven-live".
- Mantle is actively evolving (e.g. the Jan-2026 move toward Ethereum-blob DA / ZK rollup). Treat the fee-mechanic rules (MNT008) as "flag for human review" and confirm exact current behavior against the official Mantle docs before relying on any single fix.
- It flags risk; it does not guarantee a contract is Mantle-safe. Use it alongside normal testing and audits.

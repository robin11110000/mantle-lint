# mantle-migrate-lint

**Catch the Ethereum-L1 assumptions that silently break when you migrate a Solidity contract to Mantle.**

Most contracts "deploy fine" on Mantle because it's EVM-compatible — and then misbehave in production, because a handful of L1 assumptions are no longer true. `mantle-migrate-lint` is a static analyzer that flags those exact assumptions, explains *why* each one breaks on Mantle, gives a concrete fix, and proves its findings with a reproducible test suite. It runs in your terminal or as a CI gate, with zero runtime dependencies (just Python 3.8+).

It is **Mantle-specific by design** — not a generic Solidity linter. Every rule maps to a documented divergence between Mantle and Ethereum mainnet.

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

## CI integration

`.github/workflows/mantle-lint.yml` runs the linter on every PR, uploads **SARIF** so findings appear inline in the GitHub "Files changed" view, and fails the check on HIGH-severity issues. That turns "did anyone remember the Mantle gotchas?" into an automatic gate on the migration PR.

---

## Reproducible verification

```bash
python3 tests/test_rules.py      # or: python3 -m pytest -q
```

Each rule has a positive case it must catch and the clean contract must stay silent; the ERC-20 `transfer(to, amount)` case asserts the tool does **not** false-positive on token transfers.

---

## How this maps to the AI Awakening — AI DevTools (Tencent Cloud) scorecard

| Part B row (pts) | How this tool scores it |
|---|---|
| Optimization / audit output quality (13) | Code-level, Mantle-specific findings with concrete fixes — not generic LLM commentary |
| Developer productivity impact (10) | Drops into CLI + GitHub PR/CI with SARIF inline annotations and exit-code gating |
| Verifiability & benchmarking (10) | Deterministic rules + a reproducible test suite; clean vs. vulnerable fixtures prove signal |
| Execution & demo (5) | Runs end-to-end out of the box; another dev can reproduce from this README |
| Tencent Cloud + Mantle integration depth (12) | **You add this** — see below |

This is the engine. To complete the track, layer **AI** and **Tencent Cloud** on top (next section).

---

## Roadmap to a full submission (what to add for the win)

1. **AI explanation/triage layer (host inference on Tencent Cloud → claims the 12-pt integration row).** For each finding, call an LLM to (a) write a context-aware patch diff for *this* contract, and (b) rank exploitability. Keep the deterministic rules as the ground truth so the AI augments rather than hallucinates — this is exactly the "beyond generic LLM commentary" bar the rubric sets.
2. **Real benchmarking.** For gas-related rules, deploy before/after versions to Mantle Sepolia and attach measured gas deltas to each finding — turning recommendations into proven numbers.
3. **AST upgrade (production hardening).** Swap the lexical matcher for a real Solidity AST (`@solidity-parser/parser` or the solc AST). The rule structure is already isolated for this.
4. **Auto-fix, conservatively.** Some rules (e.g. `transfer` → `call{value:}`) can be auto-rewritten, but blind Solidity rewriting is unsafe (reentrancy ordering), so any auto-fix should emit a reviewable diff, never silently mutate code.

## Honest limitations

- This MVP uses a lexical scanner, not a full AST, so it favors precision on the documented patterns over exhaustive dataflow coverage.
- Mantle is actively evolving (e.g. the Jan-2026 move toward Ethereum-blob DA / ZK rollup). Treat the fee-mechanic rules (MNT008) as "flag for human review" and confirm exact current behavior against the official Mantle docs before relying on any single fix.
- It flags risk; it does not guarantee a contract is Mantle-safe. Use it alongside normal testing and audits.

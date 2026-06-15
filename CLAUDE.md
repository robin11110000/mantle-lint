# CLAUDE.md — mantle-migrate-lint

> Project context for Claude Code. Summarizes what the project is, why it's built
> this way, what's done, and what to build next. Read at the start of every session.

## What this project is

`mantle-migrate-lint` is a **Mantle-specific static analyzer for Solidity**. It
flags Ethereum-L1 assumptions that survive "EVM compatibility" but silently break
after migrating a contract to Mantle. For each issue it reports the rule, severity,
exact line, why it breaks on Mantle, and a concrete fix. Runs in the terminal or as
a CI gate, with **zero runtime dependencies** (Python 3.8+). Intentionally NOT a
generic Solidity linter — every rule maps to a documented Mantle vs. L1 divergence.

## Why it exists (core Mantle facts the rules encode)

- **MNT, not ETH, is the native gas token.** Gas is rescaled from ETH-gas via a
  `tokenRatio` (price(ETH)/price(MNT)) → fixed-gas-stipend transfers
  (`.transfer()`/`.send()`) and hardcoded `{gas: N}` are risky.
  (Source: OpenZeppelin "Mantle OP-Geth Audit".)
- **Block production is per-transaction with variable timing** → `block.number` is
  NOT a ~12s clock; "blocks as time" logic (vesting, reward-per-block, deadlines)
  drifts.
- **Mantle V2 is on the OP Stack** (op-geth fork) → inherits Optimism L2 semantics
  for block-level opcodes/randomness; centralized sequencer.
- **Chain IDs:** mainnet = 5000, Sepolia testnet = 5003.
- **Caveat:** Mantle is mid-migration toward Ethereum-blob DA / ZK (Jan 2026).
  Treat fee-mechanic rule MNT008 as "flag for review"; verify against
  https://docs.mantle.xyz before relying on a fix.

## Project layout

```
mantle_lint/
  engine.py   # comment/string-aware scanner, line mapping, rule runner, dedup
  rules.py    # the 11 Mantle-specific rules (domain knowledge lives here)
  report.py   # terminal / JSON / SARIF formatters
  cli.py      # CLI entry: file/dir scan, format select, CI exit codes
examples/
  VulnerableStaking.sol  # L1-style contract; triggers 13 findings
  CleanStaking.sol       # Mantle-safe; triggers 0 (false-positive check)
tests/test_rules.py      # reproducible rule tests
.github/workflows/mantle-lint.yml  # PR gate + SARIF upload to code scanning
```

## Run / verify

```bash
python3 -m mantle_lint.cli examples/VulnerableStaking.sol   # terminal report
python3 -m mantle_lint.cli ./contracts --format sarif > out.sarif
python3 -m mantle_lint.cli ./contracts --fail-on HIGH       # CI gate
python3 tests/test_rules.py                                  # 5/5 should pass
```

Expected: VulnerableStaking -> 7 HIGH / 4 MEDIUM / 2 INFO (13 total), exit 1.
CleanStaking -> 0 findings, exit 0.

## Rule catalog (IDs)

MNT001 native transfer/2300 stipend - MNT002 hardcoded {gas:N} -
MNT003 block.number time math - MNT004 blocks-per-time constants -
MNT005 chainid==1 - MNT006 prevrandao/difficulty - MNT007 blockhash -
MNT008 gasprice/basefee - MNT009 tx.origin auth - MNT010 native balance is MNT -
MNT011 hardcoded L1 token addresses.

Rules are isolated in `rules.py` as `Rule` objects (regex pattern OR custom
detector). Engine blanks comments/strings before matching, so line numbers stay
exact and no rule matches inside comments/strings.

## Design decisions (don't undo without reason)

- **Lexical scanner, not full AST** — dependency-free, runs in CI without a
  toolchain. Rule structure is isolated so swapping in a real AST
  (@solidity-parser/parser or solc AST) is a clean upgrade, not a rewrite.
- **No blind auto-fix** — rewriting Solidity automatically is unsafe (e.g.
  reentrancy ordering for transfer -> call{value:}). Any auto-fix must emit a
  reviewable diff, never mutate silently.
- **Findings dedupe per (rule_id, line).**

## Context: this is a hackathon submission

Target: **Mantle "AI Awakening" (Turing Test Hackathon Phase 2), AI DevTools track
(sponsored by Tencent Cloud).** Judged by human sponsor/academic reps on two
scorecards (Part A Mantle-general 50 pts + Part B track-specific 50 pts), plus an
on-chain benchmarking layer. Builder is solo and time-constrained.

Current build targets these Part B rows: output quality (13), developer
productivity (10, via CLI + SARIF + CI gate), verifiability (10, via reproducible
tests + clean/vulnerable fixtures), execution & demo (5).

## NEXT STEPS (priority order — these win the remaining points)

1. **AI triage layer, inference hosted on Tencent Cloud** — claims the 12-pt
   "Tencent Cloud + Mantle integration depth" row (currently unaddressed). For each
   deterministic finding, call an LLM to (a) generate a context-aware patch diff for
   THIS contract and (b) rank exploitability. Keep deterministic rules as ground
   truth so the AI augments, never hallucinates — the "beyond generic LLM
   commentary" bar.
2. **Real on-chain benchmarking** for gas rules: deploy before/after versions to
   Mantle Sepolia, attach measured gas deltas to findings.
3. **AST upgrade** for production-grade coverage.
4. **Demo:** record an end-to-end run (code in -> findings -> AI patch -> CI gate);
   confirm reproducibility from the README.

## Communication preferences

Be direct and honest about tradeoffs/limitations. Flag when a Mantle-specific claim
needs verification against live docs rather than asserting it. Prioritize a
complete, working, demoable slice over breadth.

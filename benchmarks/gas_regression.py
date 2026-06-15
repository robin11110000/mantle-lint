"""
Gas-regression comparison + PR-comment generator for the benchmark harness.

Reads a fresh benchmark `results.json` and a committed baseline
`gas-snapshot.json`, computes per-scenario gas deltas + behavioural status, and
emits a Markdown PR comment. **Stdlib-only** (no web3 / solc), so it runs in CI
and in the test suite without the benchmark tooling.

Modes:
  (default)  compare results.json against gas-snapshot.json -> Markdown (stdout/--out)
  --update   (re)write gas-snapshot.json from results.json  -> set the baseline
             (run this on main after an intentional gas change)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RESULTS = os.path.join(HERE, "results.json")
DEFAULT_BASELINE = os.path.join(HERE, "gas-snapshot.json")

# Sticky-comment marker so the CI bot updates one comment instead of spamming.
MARKER = "<!-- mantle-gas-bot -->"


def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def snapshot_from_results(data: dict) -> dict:
    """Reduce a full results.json to a compact, committable baseline."""
    return {
        "rule": data.get("rule"),
        "network": data.get("network", {}),
        "scenarios": {
            s["name"]: {"gasUsed": s["gasUsed"], "status": s["status"]}
            for s in data.get("scenarios", [])
        },
    }


def build_comment(results: dict, baseline: dict) -> str:
    cur = snapshot_from_results(results)
    base_sc = baseline.get("scenarios", {})
    chain = cur.get("network", {}).get("chainId", "?")
    rule = cur.get("rule", "?")

    lines = [
        MARKER,
        f"## ⛽ Mantle gas report — {rule}",
        f"Measured on **Mantle Sepolia** (chainId {chain}). Δ is vs the committed "
        f"`gas-snapshot.json` baseline.",
        "",
        "| Scenario | status | gasUsed | baseline | Δ |",
        "|---|---|---|---|---|",
    ]
    regressions = []
    for name, s in cur["scenarios"].items():
        bg = base_sc.get(name, {}).get("gasUsed")
        if bg is None:
            delta = "_new_"
        else:
            d = s["gasUsed"] - bg
            delta = f"{d:+d}"
            if d > 0:
                regressions.append((name, d))
        st = "revert" if s["status"] == 0 else "ok"
        lines.append(f"| `{name}` | {st} | {s['gasUsed']} | "
                     f"{bg if bg is not None else 'n/a'} | {delta} |")

    lines.append("")
    summ = results.get("summary", {})
    if summ.get("before_to_greedy_reverted"):
        lines.append("- ✅ `.transfer()` to a contract recipient **reverts** on-chain "
                     "(the L1 assumption that breaks on Mantle).")
    if summ.get("after_to_greedy_succeeded"):
        lines.append("- ✅ `call{value:}` **succeeds** (the fix).")

    if regressions:
        lines.append("")
        lines.append("> ⚠️ **gas increased vs baseline:** " +
                     ", ".join(f"`{n}` (+{d})" for n, d in regressions) +
                     ". If intentional, update the baseline with "
                     "`python benchmarks/gas_regression.py --update`.")
    else:
        lines.append("")
        lines.append("> ✅ No gas regression vs the committed baseline.")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Mantle gas-regression report / baseline.")
    p.add_argument("--results", default=DEFAULT_RESULTS)
    p.add_argument("--baseline", default=DEFAULT_BASELINE)
    p.add_argument("--out", help="Write the Markdown comment here (default: stdout).")
    p.add_argument("--update", action="store_true",
                   help="Rewrite the baseline from results.json instead of comparing.")
    args = p.parse_args(argv)

    # The Markdown contains an emoji; keep stdout UTF-8 so a default Windows
    # cp1252 console can't crash on it (CI / --out file are UTF-8 already).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    try:
        results = _load(args.results)
    except (OSError, ValueError) as e:
        sys.stderr.write(f"gas_regression: cannot read results {args.results}: {e}\n")
        return 2

    if args.update:
        with open(args.baseline, "w", encoding="utf-8") as fh:
            json.dump(snapshot_from_results(results), fh, indent=2)
        print(f"wrote baseline {args.baseline}")
        return 0

    try:
        baseline = _load(args.baseline)
    except (OSError, ValueError):
        baseline = {"scenarios": {}}  # first run: everything is "new", no regression

    comment = build_comment(results, baseline)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(comment + "\n")
        print(f"wrote comment {args.out}")
    else:
        print(comment)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""mantle-migrate-lint command-line interface.

Scans Solidity (.sol) files for Mantle migration issues and reports them in
terminal, JSON, or SARIF format. Exits non-zero when findings at or above a
chosen severity threshold are present, so it can gate a CI pipeline.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List

from .engine import Finding, scan
from .report import render_json, render_sarif, render_terminal
from .rules import build_rules

_SEV_RANK = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


def _iter_sol_files(target: str):
    if os.path.isfile(target):
        if target.endswith(".sol"):
            yield target
        return
    for root, _dirs, files in os.walk(target):
        if "node_modules" in root or "/lib/" in root:
            continue
        for name in files:
            if name.endswith(".sol"):
                yield os.path.join(root, name)


def run(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="mantle-migrate-lint",
        description="Flag Ethereum-L1 assumptions that break when a contract is "
                    "migrated to Mantle.",
    )
    p.add_argument("path", help="A .sol file or a directory to scan recursively.")
    p.add_argument("--format", choices=["terminal", "json", "sarif"],
                   default="terminal", help="Output format (default: terminal).")
    p.add_argument("--no-color", action="store_true", help="Disable ANSI colors.")
    p.add_argument("--fail-on", choices=["HIGH", "MEDIUM", "LOW", "INFO", "never"],
                   default="HIGH",
                   help="Minimum severity that causes a non-zero exit (default: HIGH).")
    args = p.parse_args(argv)

    rules = build_rules()
    files = list(_iter_sol_files(args.path))
    if not files:
        sys.stderr.write(f"No .sol files found at: {args.path}\n")
        return 2

    all_findings: List[Finding] = []
    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                src = fh.read()
        except (OSError, UnicodeDecodeError) as e:
            sys.stderr.write(f"skip {fpath}: {e}\n")
            continue
        all_findings.extend(scan(fpath, src, rules))

    if args.format == "json":
        print(render_json(all_findings))
    elif args.format == "sarif":
        print(render_sarif(all_findings))
    else:
        if all_findings:
            print(render_terminal(all_findings, color=not args.no_color))
        else:
            print("  No Mantle migration issues found.")

    if args.fail_on == "never":
        return 0
    threshold = _SEV_RANK[args.fail_on]
    if any(_SEV_RANK[f.severity] >= threshold for f in all_findings):
        return 1
    return 0


def main():
    sys.exit(run())


if __name__ == "__main__":
    main()

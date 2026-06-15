"""
Optional: attach measured on-chain benchmark numbers to findings.

Reads a results.json produced by `benchmarks/bench_mnt001.py` and builds, per
rule, a compact annotation (a human note + evidence links + the raw summary).
Stdlib-only — the linter's zero-dependency guarantee is unaffected. Used only
when the `--benchmarks` flag is passed; with it off, behaviour is unchanged.
"""

from __future__ import annotations

import json
from typing import Dict, List


def _mnt001_note(data: dict) -> str:
    s = data.get("summary", {})
    net = data.get("network", {}).get("chainId", "?")
    parts: List[str] = []
    if s.get("before_to_greedy_reverted") and s.get("after_to_greedy_succeeded"):
        parts.append(".transfer() to a contract recipient REVERTED on-chain; "
                     "call{value:} SUCCEEDED")
    b, a = s.get("gas_minimal_before"), s.get("gas_minimal_after")
    if isinstance(a, int) and isinstance(b, int):
        parts.append(f"minimal-recipient gasUsed transfer {b} vs call {a} "
                     f"(delta {a - b:+d})")
    body = "; ".join(parts) if parts else "see results.json"
    return f"Measured on Mantle Sepolia ({net}): {body}."


def load_annotations(path: str) -> Dict[str, dict]:
    """Parse a results.json into {rule_id: {note, links, summary}}.
    Raises OSError / ValueError on an unreadable or malformed file."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    rule = data.get("rule")
    if not rule:
        return {}
    note = _mnt001_note(data) if rule == "MNT001" \
        else f"Measured benchmark available for {rule} (see results.json)."
    links = [s["explorer"] for s in data.get("scenarios", []) if s.get("explorer")]
    return {rule: {"note": note, "links": links, "summary": data.get("summary", {})}}


def attach(findings: List, annotations: Dict[str, dict]) -> None:
    """Attach a matching annotation to each finding in place (by rule id)."""
    for f in findings:
        ann = annotations.get(f.rule_id)
        if ann:
            f.benchmark = ann

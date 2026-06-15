"""Output formatters: human-readable terminal, JSON, and SARIF (for GitHub
code-scanning / inline PR annotations)."""

from __future__ import annotations

import json
from typing import List

from .engine import Finding

SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
_COLORS = {
    "HIGH": "\033[31m", "MEDIUM": "\033[33m", "LOW": "\033[36m",
    "INFO": "\033[90m", "RESET": "\033[0m", "BOLD": "\033[1m", "DIM": "\033[2m",
}


def _c(key: str, text: str, color: bool) -> str:
    if not color:
        return text
    return f"{_COLORS.get(key, '')}{text}{_COLORS['RESET']}"


def render_terminal(findings: List[Finding], color: bool = True) -> str:
    lines: List[str] = []
    by_file = {}
    for f in findings:
        by_file.setdefault(f.file, []).append(f)

    for file, items in by_file.items():
        items.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.line))
        lines.append("")
        lines.append(_c("BOLD", f"  {file}", color))
        for f in items:
            sev = _c(f.severity, f"{f.severity:<6}", color)
            head = f"  {sev} {_c('BOLD', f.rule_id, color)} L{f.line}:{f.col}  {f.title}"
            lines.append(head)
            lines.append(_c("DIM", f"         {f.snippet}", color))
            lines.append(f"         → {f.message}")
            lines.append(f"         {_c('BOLD','fix:', color)} {f.recommendation}")
        lines.append("")

    counts = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    summary = "  ".join(
        f"{sev}: {counts.get(sev, 0)}" for sev in ["HIGH", "MEDIUM", "LOW", "INFO"]
    )
    lines.append(_c("BOLD", f"  Summary  {summary}  (total {len(findings)})", color))
    return "\n".join(lines)


def render_json(findings: List[Finding]) -> str:
    return json.dumps(
        [
            {
                "ruleId": f.rule_id, "title": f.title, "severity": f.severity,
                "category": f.category, "file": f.file, "line": f.line, "col": f.col,
                "snippet": f.snippet, "message": f.message,
                "recommendation": f.recommendation, "references": f.references,
            }
            for f in findings
        ],
        indent=2,
    )


_SARIF_LEVEL = {"HIGH": "error", "MEDIUM": "warning", "LOW": "note", "INFO": "note"}


def render_sarif(findings: List[Finding]) -> str:
    rule_ids = {}
    for f in findings:
        rule_ids.setdefault(f.rule_id, f)
    rules = [
        {
            "id": rid,
            "name": ex.title,
            "shortDescription": {"text": ex.title},
            "fullDescription": {"text": ex.message},
            "helpUri": ex.references[0] if ex.references else "",
            "properties": {"category": ex.category, "severity": ex.severity},
        }
        for rid, ex in rule_ids.items()
    ]
    results = [
        {
            "ruleId": f.rule_id,
            "level": _SARIF_LEVEL.get(f.severity, "note"),
            "message": {"text": f"{f.message} Fix: {f.recommendation}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": f.file},
                        "region": {"startLine": f.line, "startColumn": f.col},
                    }
                }
            ],
        }
        for f in findings
    ]
    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "mantle-migrate-lint",
                        "informationUri": "https://docs.mantle.xyz",
                        "version": "0.1.0",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)

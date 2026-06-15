"""
Scanning engine for mantle-migrate-lint.

Design notes
------------
This MVP deliberately uses a *lexical* scanner (comment/string-aware regex)
rather than a full Solidity AST. That choice keeps the tool dependency-free and
runnable anywhere (just Python 3.8+), which matters for a hackathon demo and for
dropping it into CI without a toolchain. The rule engine is structured so that a
production version can swap the matcher for a real AST walker
(e.g. @solidity-parser/parser or the solc AST) without rewriting the rules.

To avoid false positives, source is first passed through `blank_noncode`, which
replaces the *contents* of comments and string/hex literals with spaces while
preserving every newline. That means reported line/column numbers stay exact and
rules never match inside a comment or a string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class Finding:
    rule_id: str
    title: str
    severity: str          # HIGH | MEDIUM | LOW | INFO
    category: str
    file: str
    line: int
    col: int
    snippet: str
    message: str
    recommendation: str
    references: List[str] = field(default_factory=list)
    # Optional AI-triage annotations (populated only when --ai is on). When None,
    # output is identical to the deterministic-only run.
    ai_exploitability: Optional[str] = None   # "low" | "medium" | "high"
    ai_reason: Optional[str] = None           # one-line justification
    ai_patch: Optional[str] = None            # reviewable unified diff (suggestion)


@dataclass
class Rule:
    id: str
    title: str
    severity: str
    category: str
    message: str
    recommendation: str
    references: List[str] = field(default_factory=list)
    # A rule either supplies a compiled `pattern` or a custom `detector`.
    pattern: Optional[re.Pattern] = None
    # Optional refinement: return True to keep a regex match, False to drop it.
    validate: Optional[Callable[[re.Match, str], bool]] = None
    # Fully custom detector: (code, blanked) -> list of (offset, matched_text)
    detector: Optional[Callable[[str, str], List]] = None


def blank_noncode(src: str) -> str:
    """Replace comment and string-literal *contents* with spaces, preserving
    newlines and overall length so offsets/line numbers stay aligned."""
    out = []
    i, n = 0, len(src)
    state = "code"        # code | line_comment | block_comment | string
    quote = ""
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                out.append("  "); i += 2; state = "line_comment"; continue
            if c == "/" and nxt == "*":
                out.append("  "); i += 2; state = "block_comment"; continue
            if c == '"' or c == "'":
                quote = c; out.append(c); i += 1; state = "string"; continue
            out.append(c); i += 1; continue
        if state == "line_comment":
            if c == "\n":
                out.append("\n"); i += 1; state = "code"; continue
            out.append(" " if c != "\t" else "\t"); i += 1; continue
        if state == "block_comment":
            if c == "*" and nxt == "/":
                out.append("  "); i += 2; state = "code"; continue
            out.append("\n" if c == "\n" else (" " if c != "\t" else "\t")); i += 1; continue
        if state == "string":
            if c == "\\":                      # escape: blank this + next char
                out.append("  " if nxt != "\n" else " \n"); i += 2; continue
            if c == quote:
                out.append(c); i += 1; state = "code"; continue
            out.append("\n" if c == "\n" else " "); i += 1; continue
    return "".join(out)


def _line_starts(src: str) -> List[int]:
    starts = [0]
    for m in re.finditer("\n", src):
        starts.append(m.end())
    return starts


def _locate(offset: int, starts: List[int]) -> (int, int):
    # binary search for the line containing `offset`
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1, offset - starts[lo] + 1   # 1-based line, 1-based col


def top_level_arg_count(blanked: str, open_paren_idx: int) -> int:
    """Given index of a '(' in blanked source, count top-level comma-separated
    args inside the matching parentheses. Returns 0 for an empty arg list."""
    depth = 0
    i = open_paren_idx
    n = len(blanked)
    commas = 0
    saw_content = False
    while i < n:
        ch = blanked[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        elif ch == "," and depth == 1:
            commas += 1
        elif depth == 1 and not ch.isspace():
            saw_content = True
        i += 1
    if not saw_content:
        return 0
    return commas + 1


def scan(path: str, src: str, rules: List[Rule]) -> List[Finding]:
    blanked = blank_noncode(src)
    raw_lines = src.split("\n")
    starts = _line_starts(src)
    findings: List[Finding] = []

    for rule in rules:
        matches = []
        if rule.detector:
            matches = rule.detector(src, blanked)
        elif rule.pattern:
            for m in rule.pattern.finditer(blanked):
                if rule.validate and not rule.validate(m, blanked):
                    continue
                matches.append((m.start(), m.group(0)))

        for offset, _text in matches:
            line, col = _locate(offset, starts)
            snippet = raw_lines[line - 1].strip() if line - 1 < len(raw_lines) else ""
            findings.append(Finding(
                rule_id=rule.id, title=rule.title, severity=rule.severity,
                category=rule.category, file=path, line=line, col=col,
                snippet=snippet, message=rule.message,
                recommendation=rule.recommendation, references=list(rule.references),
            ))

    findings.sort(key=lambda f: (f.line, f.col, f.rule_id))
    # Collapse duplicate hits of the same rule on the same line (keep earliest).
    seen = set()
    deduped: List[Finding] = []
    for f in findings:
        key = (f.rule_id, f.line)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    return deduped

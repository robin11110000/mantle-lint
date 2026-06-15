"""
AI triage layer for mantle-migrate-lint (OPTIONAL, opt-in via `--ai`).

Stdlib-only (urllib / json / hashlib / os) so the core tool keeps its
zero-dependency, no-network guarantee whenever the flag is OFF. Importing this
module does nothing on its own; nothing here runs unless `triage()` is called.

It talks to a SELF-HOSTED, OpenAI-compatible `/v1/chat/completions` endpoint
(e.g. vLLM or Ollama on Tencent Cloud HAI/CVM). Design invariants:

  * The deterministic rule findings are GROUND TRUTH. The model only *annotates*
    existing findings with (a) a reviewable unified-diff patch scoped to this
    contract and (b) an exploitability ranking. It MUST NOT invent findings.
  * Patches are SUGGESTIONS for human review only — never auto-applied
    (CLAUDE.md: no blind auto-fix).
  * Malformed/missing model output -> skip enrichment for that finding rather
    than guessing.
  * temperature 0 + a disk cache keyed on (rule_id, code window, model) so demo
    runs are repeatable and the endpoint isn't re-called.
  * If the endpoint is unreachable, warn and fall back to deterministic-only
    output. Never crash the lint because the AI is down.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

# How many source lines of context (each side) to send around a finding.
_WINDOW_RADIUS = 8
_DEFAULT_TIMEOUT = 30
_DEFAULT_CACHE_DIR = ".mantle_lint_ai_cache"
_VALID_RANKS = {"low", "medium", "high"}

_SYSTEM_PROMPT = (
    "You are a Solidity assistant specialised in Ethereum-L1 -> Mantle "
    "(OP-Stack L2) migration issues. You are given ONE finding that a "
    "deterministic linter has already CONFIRMED, plus the surrounding code. "
    "The finding is GROUND TRUTH: do not dispute it, do not hunt for other "
    "issues, and do not invent new findings. "
    "Respond with ONLY a single JSON object (no prose, no markdown fences) with "
    "exactly these keys:\n"
    '  "exploitability": one of "low", "medium", "high"\n'
    '  "reason": one concise sentence justifying that ranking for THIS code\n'
    '  "patch": a unified diff (---/+++/@@ hunks) that changes ONLY the lines '
    "needed to fix THIS finding in THIS contract, minimal and suitable for human "
    "review; do not reorder or touch unrelated code.\n"
    "The patch is a suggestion for human review only."
)


class AiConfigError(Exception):
    """Raised when --ai is requested but required configuration is missing."""


@dataclass
class AiConfig:
    base_url: str
    model: str
    api_key: Optional[str] = None
    timeout: int = _DEFAULT_TIMEOUT
    cache_dir: str = _DEFAULT_CACHE_DIR


@dataclass
class AiAnnotation:
    exploitability: str
    reason: str
    patch: str


def load_config() -> AiConfig:
    """Build an AiConfig from the environment. Fails clearly if the required
    values for a self-hosted endpoint are absent."""
    base_url = (os.environ.get("MANTLE_LINT_AI_BASE_URL") or "").strip()
    model = (os.environ.get("MANTLE_LINT_AI_MODEL") or "").strip()
    if not base_url:
        raise AiConfigError(
            "MANTLE_LINT_AI_BASE_URL is not set. Point it at your self-hosted "
            "OpenAI-compatible endpoint (e.g. http://<host>:8000/v1)."
        )
    if not model:
        raise AiConfigError(
            "MANTLE_LINT_AI_MODEL is not set. Set it to the model name your "
            "endpoint serves (e.g. the vLLM/Ollama model id)."
        )
    timeout_raw = (os.environ.get("MANTLE_LINT_AI_TIMEOUT") or "").strip()
    try:
        timeout = int(timeout_raw) if timeout_raw else _DEFAULT_TIMEOUT
    except ValueError:
        timeout = _DEFAULT_TIMEOUT
    cache_dir = (os.environ.get("MANTLE_LINT_AI_CACHE_DIR") or "").strip() \
        or _DEFAULT_CACHE_DIR
    api_key = os.environ.get("MANTLE_LINT_AI_API_KEY") or None
    return AiConfig(base_url=base_url, model=model, api_key=api_key,
                    timeout=timeout, cache_dir=cache_dir)


def _default_warn(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def code_window(src: str, line: int, radius: int = _WINDOW_RADIUS) -> str:
    """Return a line-numbered window of `src` centred on 1-based `line`, with
    the finding line marked. Empty string if no source is available."""
    if not src:
        return ""
    lines = src.split("\n")
    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    out = []
    for n in range(start, end + 1):
        marker = ">>" if n == line else "  "
        out.append(f"{n:>4} {marker} {lines[n - 1]}")
    return "\n".join(out)


def _cache_key(rule_id: str, window: str, model: str) -> str:
    h = hashlib.sha256()
    h.update(rule_id.encode("utf-8"))
    h.update(b"\x00")
    h.update(window.encode("utf-8"))
    h.update(b"\x00")
    h.update(model.encode("utf-8"))
    return h.hexdigest()


class _Cache:
    def __init__(self, directory: str):
        self.dir = directory

    def _path(self, key: str) -> str:
        return os.path.join(self.dir, key + ".json")

    def get(self, key: str) -> Optional[dict]:
        try:
            with open(self._path(key), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def set(self, key: str, obj: dict) -> None:
        try:
            os.makedirs(self.dir, exist_ok=True)
            with open(self._path(key), "w", encoding="utf-8") as fh:
                json.dump(obj, fh)
        except OSError:
            pass  # caching is best-effort; never fail the lint over it


def _user_prompt(finding, window: str) -> str:
    return (
        f"Rule: {finding.rule_id} ({finding.severity}) - {finding.title}\n"
        f"Why it breaks on Mantle: {finding.message}\n"
        f"Deterministic recommendation: {finding.recommendation}\n"
        f"File: {finding.file}\n"
        f"Finding is on line {finding.line} (marked '>>' below).\n\n"
        f"Code context:\n{window}\n"
    )


def _strip_fences(text: str) -> str:
    """Remove a leading ```json / ``` fence and trailing ``` if the model wraps
    its JSON in a markdown code block."""
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _validate(obj) -> Optional[AiAnnotation]:
    """Strict-schema validation. Returns AiAnnotation or None (never guesses)."""
    if not isinstance(obj, dict):
        return None
    rank = obj.get("exploitability")
    reason = obj.get("reason")
    patch = obj.get("patch")
    if not isinstance(rank, str) or not isinstance(reason, str) \
            or not isinstance(patch, str):
        return None
    rank_norm = rank.strip().lower()
    if rank_norm == "med":
        rank_norm = "medium"
    if rank_norm not in _VALID_RANKS:
        return None
    reason_clean = reason.strip().splitlines()[0].strip() if reason.strip() else ""
    if not reason_clean or not patch.strip():
        return None
    return AiAnnotation(exploitability=rank_norm, reason=reason_clean,
                        patch=patch.rstrip("\n"))


def _parse_content(content: str) -> Optional[AiAnnotation]:
    try:
        obj = json.loads(_strip_fences(content))
    except ValueError:
        return None
    return _validate(obj)


def _call_endpoint(config: AiConfig, finding, window: str) -> str:
    """POST one chat-completion request; return the assistant message content.
    Raises urllib/OS errors (caught by the caller) on transport failure."""
    payload = {
        "model": config.model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(finding, window)},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    url = config.base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if config.api_key:
        req.add_header("Authorization", f"Bearer {config.api_key}")
    with urllib.request.urlopen(req, timeout=config.timeout) as resp:
        body = resp.read().decode("utf-8")
    obj = json.loads(body)
    return obj["choices"][0]["message"]["content"]


def triage(findings: List, sources: Dict[str, str], config: AiConfig,
           warn: Callable[[str], None] = _default_warn) -> List:
    """Annotate each finding in place with AI exploitability + a patch suggestion.

    `sources` maps file path -> full source text. Findings are mutated and also
    returned for convenience. Network/endpoint failure degrades gracefully:
    a warning is emitted and remaining findings keep their deterministic output.
    """
    cache = _Cache(config.cache_dir)
    endpoint_down = False
    for f in findings:
        if endpoint_down:
            break
        window = code_window(sources.get(f.file, ""), f.line)
        key = _cache_key(f.rule_id, window, config.model)

        ann = None
        cached = cache.get(key)
        if cached is not None:
            ann = _validate(cached)
        else:
            try:
                content = _call_endpoint(config, f, window)
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                warn(f"mantle-lint: AI endpoint unreachable ({e}); falling back "
                     f"to deterministic-only output for remaining findings.")
                endpoint_down = True
                break
            ann = _parse_content(content)
            if ann is not None:
                cache.set(key, {"exploitability": ann.exploitability,
                                "reason": ann.reason, "patch": ann.patch})

        if ann is None:
            warn(f"mantle-lint: AI returned unusable output for {f.rule_id} at "
                 f"{f.file}:{f.line}; keeping deterministic finding only.")
            continue

        f.ai_exploitability = ann.exploitability
        f.ai_reason = ann.reason
        f.ai_patch = ann.patch
    return findings

"""
Smoke test for the self-hosted AI endpoint used by `--ai`.

Stdlib-only. Verifies, in order:
  1. config is present (MANTLE_LINT_AI_BASE_URL / MANTLE_LINT_AI_MODEL),
  2. the OpenAI-compatible endpoint is reachable and returns a chat completion,
  3. a real deterministic finding round-trips through mantle_lint.ai.triage
     end-to-end (exploitability + patch attached).

Run it on the machine that will call the endpoint (e.g. through your SSH tunnel):

  # bash
  export MANTLE_LINT_AI_BASE_URL=http://localhost:11434/v1
  export MANTLE_LINT_AI_MODEL=qwen2.5-coder:3b
  python scripts/ai_smoke.py

Exit code 0 = all checks passed; non-zero = a check failed (message on stderr).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mantle_lint import ai  # noqa: E402
from mantle_lint.engine import scan  # noqa: E402
from mantle_lint.rules import build_rules  # noqa: E402

EXAMPLE = os.path.join(ROOT, "examples", "VulnerableStaking.sol")


def _fail(msg: str) -> int:
    sys.stderr.write(f"FAIL: {msg}\n")
    return 1


def check_reachable(cfg: "ai.AiConfig") -> bool:
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg.model,
        "temperature": 0,
        "messages": [{"role": "user", "content": "Reply with the single word OK."}],
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    if cfg.api_key:
        req.add_header("Authorization", f"Bearer {cfg.api_key}")
    with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    reply = body["choices"][0]["message"]["content"]
    print(f"[2/3] endpoint reachable; model replied: {reply.strip()[:80]!r}")
    return True


def check_triage(cfg: "ai.AiConfig") -> bool:
    with open(EXAMPLE, "r", encoding="utf-8") as fh:
        src = fh.read()
    findings = scan(EXAMPLE, src, build_rules())
    target = next((f for f in findings if f.rule_id == "MNT001"), findings[0])
    ai.triage([target], {EXAMPLE: src}, cfg, warn=lambda m: sys.stderr.write(m + "\n"))
    if not target.ai_exploitability:
        return False
    print(f"[3/3] triage round-trip OK for {target.rule_id} "
          f"(L{target.line}): exploitability={target.ai_exploitability}")
    print("      reason:", target.ai_reason)
    print("      patch (first line):", (target.ai_patch or "").split(chr(10))[0])
    return True


def main() -> int:
    try:
        cfg = ai.load_config()
    except ai.AiConfigError as e:
        return _fail(str(e))
    print(f"[1/3] config OK: base_url={cfg.base_url} model={cfg.model}")

    try:
        check_reachable(cfg)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        return _fail(f"endpoint not reachable / not OpenAI-compatible: {e}")

    try:
        if not check_triage(cfg):
            return _fail("triage produced no annotation (model output failed schema).")
    except Exception as e:  # noqa: BLE001 - smoke test reports any failure plainly
        return _fail(f"triage round-trip errored: {e}")

    print("\nALL CHECKS PASSED - the --ai endpoint is live and working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

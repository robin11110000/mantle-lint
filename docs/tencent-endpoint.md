# Standing up the self-hosted AI endpoint on Tencent Cloud (Ollama, CPU)

This is the runbook for the `--ai` triage layer's inference endpoint. It targets
**Ollama on a CPU-only Tencent Cloud instance** — no GPU quota required — serving a
small coder model behind an **OpenAI-compatible** `/v1/chat/completions` API, which
is exactly what `--ai` already speaks.

> The linter stays zero-dependency; this endpoint is an *external* service the
> `--ai` flag calls into. With `--ai` off, none of this is touched.

> ⚠️ **Verify Tencent-console specifics in your own account.** Instance families,
> region names, and the HAI vs. CVM UI change over time — the steps below are the
> shape of the process, not exact button labels. The Ollama/`--ai` steps are exact.

---

## 1. Provision a CPU instance

In the Tencent Cloud console, create a **CVM** (or a HAI instance) with:

- **OS:** Ubuntu 22.04 LTS
- **CPU/RAM:** ≥ 4 vCPU / **8 GB RAM** for a 3B model; 4 GB is enough for a 1.5B
  model. (CPU inference is slow but fine for a demo — the response cache makes
  repeats instant.)
- **Disk:** 20 GB+ (model weights are a few GB).
- **Network:** a public IP so you can SSH in.

Note the instance's public IP and your SSH user (often `ubuntu`).

> 💸 CPU instances are cheap, but **stop the instance when you're done** so it
> doesn't bill idle.

---

## 2. Install Ollama and pull a model

SSH into the instance, then:

```bash
curl -fsSL https://ollama.com/install.sh | sh   # installs + starts a systemd service
ollama pull qwen2.5-coder:3b                     # or qwen2.5-coder:1.5b if RAM < 8 GB
```

Ollama listens on `127.0.0.1:11434` by default and exposes OpenAI-compatible routes
under `/v1`. Confirm it's up:

```bash
curl http://localhost:11434/v1/models
```

---

## 3. Reach it securely from your machine

**Recommended — SSH tunnel (no open ports, no auth to manage):**

```bash
# on your laptop; keep this shell open while you use --ai
ssh -L 11434:localhost:11434 ubuntu@<instance-public-ip>
```

Then the endpoint is `http://localhost:11434/v1` locally. Nothing is exposed to the
internet. This is the default the runbook assumes.

**Alternative — expose the port (less safe):** set `OLLAMA_HOST=0.0.0.0`, restart
Ollama, and open TCP **11434** in the instance's security group **restricted to your
IP**. Ollama has **no built-in auth**, so if you expose it, put Caddy/nginx in front
to require a bearer token and set `MANTLE_LINT_AI_API_KEY` accordingly. Prefer the
tunnel unless you have a reason not to.

---

## 4. Wire up `--ai` and verify

On the machine that runs the linter (with the tunnel open):

```bash
# bash
export MANTLE_LINT_AI_BASE_URL=http://localhost:11434/v1
export MANTLE_LINT_AI_MODEL=qwen2.5-coder:3b
# export MANTLE_LINT_AI_API_KEY=...   # only if you put an auth proxy in front

python scripts/ai_smoke.py            # 3-step health check (config, reachable, triage)
```

PowerShell equivalent:

```powershell
$env:MANTLE_LINT_AI_BASE_URL="http://localhost:11434/v1"
$env:MANTLE_LINT_AI_MODEL="qwen2.5-coder:3b"
python scripts\ai_smoke.py
```

When the smoke test prints `ALL CHECKS PASSED`, run the real thing:

```bash
python3 -m mantle_lint.cli examples/VulnerableStaking.sol --ai
```

---

## Notes & honest caveats

- **The model must return JSON matching our schema** (`exploitability`, `reason`,
  `patch`). `ai.py` strips ```` ```json ```` fences and validates strictly; if a
  small model emits something off-schema, that finding **keeps its deterministic
  output** (graceful skip, never a crash). A coder/instruct model like
  `qwen2.5-coder` is the most reliable at this on CPU.
- **Reproducibility:** requests are sent at temperature 0 and cached on disk
  (`.mantle_lint_ai_cache/`), so a demo replays identically and doesn't re-hit the
  endpoint.
- **Latency:** CPU inference can take several seconds per finding the first time;
  the cache makes subsequent runs instant.
- The deterministic findings remain ground truth — the model only annotates them.

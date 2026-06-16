# mantle-migrate-lint — web frontend

A **static, client-side** frontend for the `catch` engine — **no backend**. Paste a
Solidity contract; it's **compiled with the real `solc` (v0.8.23, Mantle's
recommended) in a Web Worker** and analyzed on its **canonical AST**, all in the
browser.

- `index.html` — the UI (single page, no framework).
- `solc-worker.js` — loads `soljson` v0.8.23 (~9 MB, lazily, once) and compiles
  the pasted source off the main thread.
- `analyze.js` — walks the **solc AST** for type-aware findings (e.g. an ERC-20
  `token.transfer(to, amount)` is never flagged as MNT001), and surfaces compiler
  errors/warnings + a bytecode-size / creation-gas estimate.
- `lint.js` — the **regex** engine (a faithful JS port of the Python rules). Used
  as the **fallback** when source doesn't compile (partial snippets, wrong pragma)
  or if the compiler can't load — so the page never breaks.
- `samples.js` — the two example contracts, mirrored from `examples/*.sol`.
- `parity.mjs` — parity test for the **regex** engine vs the **canonical Python
  CLI** (CleanStaking → 0, VulnerableStaking → 13).
- `vercel.json` — static deploy config.

The **fix** (`--ai`) and **prove** (gas bot) pillars are intentionally *not* in the
browser (they need a model endpoint and a testnet wallet); the page links out to
the demo, PR #1, and the on-chain receipts.

## How analysis runs

1. **Compile & analyze** → worker compiles with solc → if it compiles, findings come
   from the **AST** (most precise) + compiler warnings + size/gas.
2. If it **doesn't compile**, the page shows the compiler errors and falls back to
   the **regex** engine for findings (works on any snippet).
3. If the compiler can't load at all, it falls back to regex entirely.

## Verify the engines match the canonical Python

Regex engine (no extra deps — uses the Python CLI):
```bash
node web/parity.mjs            # CleanStaking -> 0, VulnerableStaking -> 13
```

AST analyzer (against the real solc compiler in Node):
```bash
# one-time, in a scratch dir outside web/ (keeps the deploy folder clean):
mkdir ast-check && cd ast-check && npm init -y && npm i solc
# then run a script that compiles examples/*.sol with solc and calls
# web/analyze.js — it must reproduce the same 13/0 (see the commit that added
# this frontend for the exact harness). Verified: 13/0, identical to Python.
```

## Local preview

ES modules + the worker need http (not `file://`):
```bash
cd web
python -m http.server 8000      # then open http://localhost:8000
# or: npx serve .   |   vercel dev
```

## Deploy to Vercel (static, no build)

Set **Root Directory = `web`** (the repo root has `pyproject.toml`, which would
otherwise make Vercel try a Python build). Then:
```bash
cd web
vercel deploy          # preview   (or: vercel deploy --prod)
```
Or use the dashboard: Add New → Project → import the repo → **Root Directory = `web`**.

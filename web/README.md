# mantle-migrate-lint — web frontend

A **static, client-side** frontend for the `catch` engine: paste a Solidity
contract and see the Mantle migration findings live, with **no backend**. The
rules are a faithful JS port of the canonical Python engine (`mantle_lint/`).

- `index.html` — the UI (single page, no framework).
- `lint.js` — JS port of the lexical engine + 11 MNT rules (the in-browser linter).
- `samples.js` — the two example contracts, mirrored from `examples/*.sol`.
- `parity.mjs` — parity test: runs the **canonical Python CLI** and asserts the
  JS port produces identical findings (CleanStaking → 0, VulnerableStaking → 13).
- `vercel.json` — static deploy config.

The **fix** (`--ai`) and **prove** (`--benchmarks` / gas bot) pillars are
intentionally *not* in the browser (they need a model endpoint and a testnet
wallet); the page links out to the demo, PR #1, and the receipts.

## Verify the JS port matches Python

From the repo root (needs Python + the package importable, and Node):

```bash
node web/parity.mjs
```

## Local preview

ES modules need to be served over http (not `file://`), so use any static server:

```bash
cd web
python -m http.server 8000      # then open http://localhost:8000
# or: npx serve .   |   vercel dev
```

## Deploy to Vercel (static, no build)

```bash
npm i -g vercel        # if you don't have the CLI
cd web                 # deploy this folder as the site root
vercel login           # first time only
vercel deploy          # preview URL  (or: vercel deploy --prod  for production)
```

First run will ask to set up/link a project — accept the defaults (framework:
none, no build command, output dir `.`). Vercel serves the static files and
prints the deployment URL.

// Parity check: the JS port (lint.js) MUST match the canonical Python linter.
//
// For each fixture it runs `python -m mantle_lint.cli <file> --format json`
// (the source of truth) and compares the (ruleId, line, severity) set against
// web/lint.js scan() output. Exits non-zero on any mismatch.
//
//   node web/parity.mjs
//
// Also asserts the headline invariants: CleanStaking -> 0, VulnerableStaking -> 13.

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { scan } from "./lint.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");

const PY = process.env.PYTHON || "python";

function pythonFindings(relPath) {
  // --fail-on never so the CLI exits 0 even when HIGH findings exist (the JSON
  // is identical); otherwise execFileSync would throw on the non-zero gate exit.
  const out = execFileSync(
    PY, ["-m", "mantle_lint.cli", relPath, "--format", "json", "--fail-on", "never"],
    { cwd: ROOT, encoding: "utf-8" });
  return JSON.parse(out).map((f) => `${f.ruleId}:${f.line}:${f.severity}`).sort();
}

function jsFindings(relPath) {
  const src = readFileSync(join(ROOT, relPath), "utf-8");
  return scan(src).map((f) => `${f.ruleId}:${f.line}:${f.severity}`).sort();
}

const cases = [
  { file: "examples/CleanStaking.sol", expectCount: 0 },
  { file: "examples/VulnerableStaking.sol", expectCount: 13 },
];

let failed = 0;
for (const { file, expectCount } of cases) {
  const py = pythonFindings(file);
  const js = jsFindings(file);
  const same = py.length === js.length && py.every((v, i) => v === js[i]);
  const countOk = js.length === expectCount;

  if (same && countOk) {
    console.log(`ok  ${file}: ${js.length} findings, JS == Python`);
  } else {
    failed++;
    console.error(`FAIL ${file}: count=${js.length} (want ${expectCount}), parity=${same}`);
    const pset = new Set(py), jset = new Set(js);
    const onlyPy = py.filter((v) => !jset.has(v));
    const onlyJs = js.filter((v) => !pset.has(v));
    if (onlyPy.length) console.error("   only in Python:", onlyPy.join(", "));
    if (onlyJs.length) console.error("   only in JS:    ", onlyJs.join(", "));
  }
}

if (failed) {
  console.error(`\n${failed} parity check(s) failed.`);
  process.exit(1);
}
console.log("\nparity OK — JS port matches the canonical Python rules.");

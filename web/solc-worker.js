// Web Worker: loads the real Solidity compiler (Mantle-recommended v0.8.23) and
// compiles the pasted source off the main thread. Returns solc standard-JSON
// output (AST + diagnostics + bytecode/gas). The compiler (~9 MB) loads once,
// lazily, on the first compile. If anything fails, we post {ok:false} and the
// page falls back to the regex engine — the UI never breaks.

/* eslint-disable no-undef */
const SOLJSON = "https://binaries.soliditylang.org/bin/soljson-v0.8.23+commit.f704f362.js";

let compile = null;

function setup() {
  // After importScripts, the emscripten module is available as `Module`.
  if (typeof Module === "undefined") throw new Error("soljson failed to load");
  if ("_solidity_compile" in Module) {
    const c = Module.cwrap("solidity_compile", "string", ["string", "number", "number"]);
    compile = (input) => c(input, 0, 0); // no import callback (single-file source)
  } else if ("_compileStandard" in Module) {
    const c = Module.cwrap("compileStandard", "string", ["string", "number"]);
    compile = (input) => c(input, 0);
  } else {
    throw new Error("no solc compile entrypoint found");
  }
}

self.onmessage = (e) => {
  const { source } = e.data || {};
  try {
    if (!compile) {
      importScripts(SOLJSON); // synchronous in a worker; blocks only this thread
      setup();
    }
    const input = {
      language: "Solidity",
      sources: { "Contract.sol": { content: source } },
      settings: {
        optimizer: { enabled: true, runs: 200 },
        outputSelection: {
          "*": { "*": ["evm.bytecode.object", "evm.gasEstimates"], "": ["ast"] },
        },
      },
    };
    const output = JSON.parse(compile(JSON.stringify(input)));
    self.postMessage({ ok: true, output });
  } catch (err) {
    self.postMessage({ ok: false, error: String((err && err.message) || err) });
  }
};

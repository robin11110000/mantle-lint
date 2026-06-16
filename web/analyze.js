// AST-based analysis for the frontend, built on the canonical solc AST.
//
// Given solc standard-JSON output + the source text, this produces:
//   - diagnostics: compiler errors/warnings (things the lexical scanner can't see)
//   - contracts:   bytecode size + solc's own creation-gas estimate
//   - findings:    the MNT rules evaluated on the AST (type-aware, so e.g. an
//                  ERC-20 `token.transfer(to, amount)` is never flagged as MNT001)
//
// Reuses the canonical rule metadata (id/title/severity/message/recommendation)
// from lint.js — only the *detection* differs (AST nodes vs regex). When source
// doesn't compile, the caller falls back to lint.js (regex) so partial snippets
// still work.

import { RULES, KNOWN_L1_ADDRESSES, SEVERITY_ORDER } from "./lint.js";

const META = Object.fromEntries(RULES.map((r) => [r.id, r]));

// solc `src` offsets are UTF-8 BYTE offsets, so map them through the encoded
// bytes (a multi-byte char like an em-dash in a comment would otherwise shift
// every later slice). Cached per source for speed.
let _cacheSrc = null, _cacheBytes = null;
const NL = 10; // '\n'
function bytesOf(source) {
  if (_cacheSrc !== source) { _cacheSrc = source; _cacheBytes = new TextEncoder().encode(source); }
  return _cacheBytes;
}

function locFromSrc(src, source) {
  const start = parseInt(String(src).split(":")[0], 10) || 0;
  const b = bytesOf(source);
  let line = 1, col = 1;
  for (let i = 0; i < start && i < b.length; i++) {
    if (b[i] === NL) { line++; col = 1; } else { col++; }
  }
  return { start, line, col };
}

function srcText(src, source) {
  const [start, len] = String(src).split(":").map((x) => parseInt(x, 10));
  const b = bytesOf(source);
  return new TextDecoder().decode(b.slice(start, start + (len || 0)));
}

const typeStr = (n) => (n && n.typeDescriptions && n.typeDescriptions.typeString) || "";
const isBase = (n, name) => n && n.nodeType === "Identifier" && n.name === name;

// Walk every AST node, calling cb(node).
function walk(node, cb) {
  if (Array.isArray(node)) { for (const x of node) walk(x, cb); return; }
  if (!node || typeof node !== "object") return;
  if (typeof node.nodeType === "string") cb(node);
  for (const k of Object.keys(node)) {
    if (k === "typeDescriptions") continue;
    walk(node[k], cb);
  }
}

function detect(ast, source) {
  const hits = []; // {ruleId, src}
  const add = (ruleId, src) => hits.push({ ruleId, src });

  walk(ast, (n) => {
    switch (n.nodeType) {
      case "FunctionCall": {
        const e = n.expression;
        // MNT001: address.transfer/.send with exactly ONE arg (native value move).
        if (e && e.nodeType === "MemberAccess" && (e.memberName === "transfer" || e.memberName === "send")
            && Array.isArray(n.arguments) && n.arguments.length === 1
            && /address/.test(typeStr(e.expression))) {
          add("MNT001", n.src);
        }
        // MNT007: blockhash(...)
        if (e && e.nodeType === "Identifier" && e.name === "blockhash") add("MNT007", n.src);
        break;
      }
      case "FunctionCallOptions": {
        // MNT002: a `{gas: N}` call option (NOT `{value: ...}`).
        if (Array.isArray(n.names) && n.names.includes("gas")) add("MNT002", n.src);
        break;
      }
      case "BinaryOperation": {
        const op = n.operator;
        const L = n.leftExpression, R = n.rightExpression;
        const isBlockNumber = (x) => x && x.nodeType === "MemberAccess" && x.memberName === "number" && isBase(x.expression, "block");
        // MNT003: block.number in multiplicative time math.
        if ((op === "*" || op === "/") && (isBlockNumber(L) || isBlockNumber(R))) add("MNT003", n.src);
        // MNT005: chainid ==/!= 1
        if (op === "==" || op === "!=") {
          const isChain = (x) => x && x.nodeType === "MemberAccess" && x.memberName === "chainid";
          const isOne = (x) => x && x.nodeType === "Literal" && x.value === "1";
          if ((isChain(L) && isOne(R)) || (isChain(R) && isOne(L))) add("MNT005", n.src);
        }
        break;
      }
      case "MemberAccess": {
        // MNT006: block.prevrandao / block.difficulty
        if ((n.memberName === "prevrandao" || n.memberName === "difficulty") && isBase(n.expression, "block")) add("MNT006", n.src);
        // MNT008: tx.gasprice / block.basefee
        if (n.memberName === "gasprice" && isBase(n.expression, "tx")) add("MNT008", n.src);
        if (n.memberName === "basefee" && isBase(n.expression, "block")) add("MNT008", n.src);
        // MNT009: tx.origin
        if (n.memberName === "origin" && isBase(n.expression, "tx")) add("MNT009", n.src);
        // MNT010: address(this).balance
        if (n.memberName === "balance") {
          const x = n.expression;
          const isAddressThis = x && x.nodeType === "FunctionCall"
            && x.expression && x.expression.nodeType === "ElementaryTypeNameExpression"
            && /address/.test(typeStr(x.expression) || (x.expression.typeName && x.expression.typeName.name) || "")
            && Array.isArray(x.arguments) && x.arguments.some((a) => a && a.name === "this");
          if (isAddressThis) add("MNT010", n.src);
        }
        break;
      }
      case "VariableDeclaration":
      case "Identifier": {
        // MNT004: BLOCKS_PER_* constant (declaration or use).
        if (typeof n.name === "string" && /^BLOCKS?_PER_(DAY|HOUR|WEEK|MONTH|YEAR|MINUTE)$/i.test(n.name)) add("MNT004", n.src);
        break;
      }
      case "Literal": {
        // MNT004: a bare 7200 (blocks-per-day) literal.
        if (n.kind === "number" && (n.value === "7200" || srcText(n.src, source).replace(/_/g, "") === "7200")) add("MNT004", n.src);
        // MNT011: a known Ethereum-mainnet address literal.
        const t = srcText(n.src, source).trim();
        if (/^0x[0-9a-fA-F]{40}$/.test(t) && Object.prototype.hasOwnProperty.call(KNOWN_L1_ADDRESSES, t.toLowerCase())) add("MNT011", n.src);
        break;
      }
    }
  });
  return hits;
}

export function analyze(output, source) {
  // Diagnostics (errors + warnings) from the compiler.
  const diagnostics = (output.errors || []).map((e) => {
    const sl = e.sourceLocation;
    const line = sl ? locFromSrc(`${sl.start}:0:0`, source).line : null;
    return { severity: e.severity || "error", message: e.message || e.formattedMessage, line };
  });
  const hasError = diagnostics.some((d) => d.severity === "error");

  // Bytecode size + creation-gas estimate per contract.
  const contracts = [];
  const files = output.contracts || {};
  for (const file of Object.keys(files)) {
    for (const name of Object.keys(files[file])) {
      const c = files[file][name];
      const obj = (c.evm && c.evm.bytecode && c.evm.bytecode.object) || "";
      const creation = c.evm && c.evm.gasEstimates && c.evm.gasEstimates.creation;
      contracts.push({
        name,
        bytecodeBytes: obj ? obj.length / 2 : 0,
        creationGas: creation ? (creation.totalCost ?? null) : null,
      });
    }
  }

  // AST findings (only meaningful when it compiled to an AST).
  const rawLines = source.split("\n");
  let findings = [];
  const sources = output.sources || {};
  for (const file of Object.keys(sources)) {
    const ast = sources[file] && sources[file].ast;
    if (!ast) continue;
    for (const { ruleId, src } of detect(ast, source)) {
      const m = META[ruleId];
      if (!m) continue;
      const { line, col } = locFromSrc(src, source);
      findings.push({
        ruleId, title: m.title, severity: m.severity, category: m.category,
        line, col, snippet: rawLines[line - 1] ? rawLines[line - 1].trim() : "",
        message: m.message, recommendation: m.recommendation, references: m.references.slice(),
      });
    }
  }

  // Sort + dedupe per (ruleId, line) — same contract as the regex engine.
  findings.sort((a, b) => a.line - b.line || a.col - b.col || (a.ruleId < b.ruleId ? -1 : a.ruleId > b.ruleId ? 1 : 0));
  const seen = new Set();
  findings = findings.filter((f) => {
    const k = f.ruleId + ":" + f.line;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });

  return { diagnostics, contracts, findings, hasError, SEVERITY_ORDER };
}

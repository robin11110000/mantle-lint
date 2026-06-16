// Client-side JS port of the mantle-migrate-lint lexical engine + rules.
//
// GUARDRAIL: the Python package (mantle_lint/) is the CANONICAL implementation.
// This is a faithful in-browser mirror so the frontend needs no backend. It is
// parity-tested against the same fixtures as Python (web/parity.mjs): it MUST
// reproduce CleanStaking -> 0 findings and VulnerableStaking -> 13 findings with
// identical (ruleId, line, severity). Keep the two in sync when rules change.
//
// Ported from mantle_lint/engine.py (blank_noncode, _locate,
// top_level_arg_count, scan) and mantle_lint/rules.py (the 11 MNT rules).

const DOCS = "https://docs.mantle.xyz";
const OZ_AUDIT = "https://www.openzeppelin.com/news/mantle-op-geth-audit";

const KNOWN_L1_ADDRESSES = {
  "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH (Ethereum mainnet)",
  "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC (Ethereum mainnet)",
  "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT (Ethereum mainnet)",
};

// Replace comment and string-literal *contents* with spaces, preserving newlines
// and overall length so offsets/line numbers stay aligned. (engine.blank_noncode)
export function blankNoncode(src) {
  const out = [];
  let i = 0;
  const n = src.length;
  let state = "code"; // code | line_comment | block_comment | string
  let quote = "";
  while (i < n) {
    const c = src[i];
    const nxt = i + 1 < n ? src[i + 1] : "";
    if (state === "code") {
      if (c === "/" && nxt === "/") { out.push("  "); i += 2; state = "line_comment"; continue; }
      if (c === "/" && nxt === "*") { out.push("  "); i += 2; state = "block_comment"; continue; }
      if (c === '"' || c === "'") { quote = c; out.push(c); i += 1; state = "string"; continue; }
      out.push(c); i += 1; continue;
    }
    if (state === "line_comment") {
      if (c === "\n") { out.push("\n"); i += 1; state = "code"; continue; }
      out.push(c !== "\t" ? " " : "\t"); i += 1; continue;
    }
    if (state === "block_comment") {
      if (c === "*" && nxt === "/") { out.push("  "); i += 2; state = "code"; continue; }
      out.push(c === "\n" ? "\n" : (c !== "\t" ? " " : "\t")); i += 1; continue;
    }
    if (state === "string") {
      if (c === "\\") { out.push(nxt !== "\n" ? "  " : " \n"); i += 2; continue; }
      if (c === quote) { out.push(c); i += 1; state = "code"; continue; }
      out.push(c === "\n" ? "\n" : " "); i += 1; continue;
    }
  }
  return out.join("");
}

function lineStarts(src) {
  const starts = [0];
  for (let i = 0; i < src.length; i++) if (src[i] === "\n") starts.push(i + 1);
  return starts;
}

// 1-based line, 1-based col for an offset. (engine._locate)
function locate(offset, starts) {
  let lo = 0, hi = starts.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (starts[mid] <= offset) lo = mid; else hi = mid - 1;
  }
  return [lo + 1, offset - starts[lo] + 1];
}

// Count top-level comma-separated args inside the parens beginning at
// openParenIdx. 0 for an empty list. (engine.top_level_arg_count)
function topLevelArgCount(blanked, openParenIdx) {
  let depth = 0, commas = 0, sawContent = false;
  for (let i = openParenIdx; i < blanked.length; i++) {
    const ch = blanked[i];
    if (ch === "(") depth += 1;
    else if (ch === ")") { depth -= 1; if (depth === 0) break; }
    else if (ch === "," && depth === 1) commas += 1;
    else if (depth === 1 && !/\s/.test(ch)) sawContent = true;
  }
  if (!sawContent) return 0;
  return commas + 1;
}

// Custom detectors return arrays of [offset, matchedText].
function detectNativeTransfer(src, blanked) {
  const out = [];
  const re = /\.(transfer|send)\s*\(/g;
  let m;
  while ((m = re.exec(blanked)) !== null) {
    const openParen = m.index + m[0].length - 1; // the '(' is the last char
    if (topLevelArgCount(blanked, openParen) === 1) out.push([m.index, m[0]]);
  }
  return out;
}

function detectL1Addresses(src, blanked) {
  const out = [];
  const re = /0x[0-9a-fA-F]{40}/g;
  let m;
  while ((m = re.exec(blanked)) !== null) {
    if (Object.prototype.hasOwnProperty.call(KNOWN_L1_ADDRESSES, m[0].toLowerCase())) {
      out.push([m.index, m[0]]);
    }
  }
  return out;
}

export const RULES = [
  { id: "MNT001", severity: "HIGH", category: "gas-token",
    title: "Native value transfer relies on the 2300-gas stipend",
    message: "`.transfer()`/`.send()` forward a fixed 2300-gas stipend. On Mantle the native token is MNT and gas is rescaled from ETH-gas via a tokenRatio, so the effective work a 2300-gas stipend buys can differ from L1 and may cause transfers to contract recipients to revert unexpectedly.",
    recommendation: "Use `(bool ok, ) = payable(to).call{value: amount}(\"\");` with an explicit success check and a reentrancy guard (checks-effects-interactions).",
    references: [OZ_AUDIT, DOCS], detector: detectNativeTransfer },
  { id: "MNT002", severity: "HIGH", category: "gas-token",
    title: "Hardcoded gas amount in external call",
    message: "A hardcoded `{gas: N}` value assumes L1 opcode gas costs. Under Mantle's MNT-denominated gas and tokenRatio scaling, a fixed gas budget can be too low (revert) or behave differently than intended.",
    recommendation: "Avoid hardcoding gas. Forward a safe portion of remaining gas, or make the limit configurable and validated against current Mantle gas behaviour.",
    references: [OZ_AUDIT, DOCS], pattern: /\{\s*gas\s*:\s*\d[\d_]*\s*\}/g },
  { id: "MNT003", severity: "MEDIUM", category: "block-timing",
    title: "block.number used in time/duration arithmetic",
    message: "Mantle generates one block per transaction with variable block time, so block.number does NOT advance on a fixed ~12s cadence. Treating block height as a clock (deadlines, vesting, reward-per-block) will be incorrect on Mantle.",
    recommendation: "Use `block.timestamp` for time-based logic. If you need per-block accounting, do not assume any fixed seconds-per-block relationship.",
    references: [DOCS], pattern: /block\.number\s*[\*/]\s*\d|\d\s*[\*/]\s*block\.number/g },
  { id: "MNT004", severity: "HIGH", category: "block-timing",
    title: "Hardcoded blocks-per-time constant assumes 12s L1 blocks",
    message: "A constant like BLOCKS_PER_DAY = 7200 (or `* 12 seconds`) bakes in Ethereum's ~12s block time. Mantle block production is per-transaction and variable, so this constant is meaningless and time-based logic built on it will drift.",
    recommendation: "Drive durations from `block.timestamp` (seconds), not from a blocks-per-period constant.",
    references: [DOCS], pattern: /BLOCKS?_PER_(?:DAY|HOUR|WEEK|MONTH|YEAR|MINUTE)|=\s*7200\b|\*\s*12\s+seconds\b/gi },
  { id: "MNT005", severity: "HIGH", category: "chain-assumption",
    title: "Hardcoded mainnet chainId (== 1)",
    message: "A `chainid == 1` check assumes Ethereum mainnet. Mantle mainnet is chainId 5000 and Mantle Sepolia testnet is 5003, so this branch will never execute (or will wrongly reject) on Mantle.",
    recommendation: "Compare against the correct Mantle chainId (5000 / 5003), or parameterise supported chainIds instead of hardcoding 1.",
    references: [DOCS], pattern: /(?:block\.)?chain[Ii]d\s*(?:==|!=)\s*1\b/g },
  { id: "MNT006", severity: "MEDIUM", category: "randomness",
    title: "Use of block.prevrandao / block.difficulty",
    message: "On Mantle (OP-Stack L2 with a centralised sequencer) block.prevrandao / block.difficulty are not a meaningful or secure entropy source and differ from L1 semantics. Any randomness derived from them is predictable.",
    recommendation: "Use a dedicated randomness source (e.g. a VRF/oracle). Never derive security-relevant randomness from block-level values.",
    references: [DOCS], pattern: /block\.(prevrandao|difficulty)\b/g },
  { id: "MNT007", severity: "MEDIUM", category: "randomness",
    title: "Use of blockhash()",
    message: "blockhash() on an L2 only covers recent blocks and is influenced by the sequencer. It is unsafe as randomness and may not behave like L1 for historical-block assumptions.",
    recommendation: "Do not use blockhash for randomness or long-range history. Use an oracle/VRF where unpredictability matters.",
    references: [DOCS], pattern: /\bblockhash\s*\(/g },
  { id: "MNT008", severity: "MEDIUM", category: "fee-mechanics",
    title: "tx.gasprice / block.basefee dependency",
    message: "Mantle's fee model (MNT gas, tokenRatio, L1-DA fee component, operator fee) differs from L1 EIP-1559. Logic that reads tx.gasprice or block.basefee — e.g. gas refunds, fee reimbursement — can compute wrong values.",
    recommendation: "Re-derive any fee/refund math against Mantle's current fee model and verify with on-chain measurements before relying on these values.",
    references: [OZ_AUDIT, DOCS], pattern: /\btx\.gasprice\b|\bblock\.basefee\b/g },
  { id: "MNT009", severity: "MEDIUM", category: "auth",
    title: "tx.origin used for authorization",
    message: "tx.origin authorization is unsafe in general, and Mantle supports native meta-transactions, which can further break the assumption that tx.origin is the intended caller.",
    recommendation: "Authorize against `msg.sender`. If you need meta-tx support, adopt a proper forwarder/permit pattern.",
    references: [OZ_AUDIT, DOCS], pattern: /\btx\.origin\b/g },
  { id: "MNT010", severity: "INFO", category: "gas-token",
    title: "address(this).balance is denominated in MNT, not ETH",
    message: "Native balance on Mantle is MNT, not ETH. If your accounting, event labels, or price math assume the native balance is ETH, values and UX will be wrong.",
    recommendation: "Confirm native-balance handling treats the unit as MNT, and that any USD/ETH conversion uses the right asset.",
    references: [DOCS], pattern: /address\s*\(\s*this\s*\)\s*\.\s*balance/g },
  { id: "MNT011", severity: "INFO", category: "chain-assumption",
    title: "Hardcoded Ethereum-mainnet contract address",
    message: "This is a well-known Ethereum-mainnet token/contract address. The equivalent asset on Mantle lives at a different address; the hardcoded value will point at nothing (or the wrong contract) on Mantle.",
    recommendation: "Replace with the Mantle deployment address (check the Mantle bridge/token list) or inject the address via constructor/config per network.",
    references: [DOCS], detector: detectL1Addresses },
];

export const SEVERITY_ORDER = { HIGH: 0, MEDIUM: 1, LOW: 2, INFO: 3 };

// Faithful port of engine.scan: match on blanked source, map offsets to
// line/col, sort by (line, col, ruleId), dedupe per (ruleId, line).
export function scan(src) {
  const blanked = blankNoncode(src);
  const rawLines = src.split("\n");
  const starts = lineStarts(src);
  const findings = [];

  for (const rule of RULES) {
    let matches = [];
    if (rule.detector) {
      matches = rule.detector(src, blanked);
    } else if (rule.pattern) {
      rule.pattern.lastIndex = 0;
      let m;
      while ((m = rule.pattern.exec(blanked)) !== null) {
        matches.push([m.index, m[0]]);
        if (m.index === rule.pattern.lastIndex) rule.pattern.lastIndex++; // guard zero-width
      }
    }
    for (const [offset] of matches) {
      const [line, col] = locate(offset, starts);
      const snippet = line - 1 < rawLines.length ? rawLines[line - 1].trim() : "";
      findings.push({
        ruleId: rule.id, title: rule.title, severity: rule.severity,
        category: rule.category, line, col, snippet,
        message: rule.message, recommendation: rule.recommendation,
        references: rule.references.slice(),
      });
    }
  }

  findings.sort((a, b) =>
    a.line - b.line || a.col - b.col || (a.ruleId < b.ruleId ? -1 : a.ruleId > b.ruleId ? 1 : 0));

  const seen = new Set();
  const deduped = [];
  for (const f of findings) {
    const key = f.ruleId + ":" + f.line;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(f);
  }
  return deduped;
}

"""
Mantle migration rule catalog.

Every rule encodes a *Mantle-specific* divergence from Ethereum L1 that can
silently change a contract's behaviour after migration. Rules are grounded in:

  - MNT is the native L2 gas token (not ETH); gas is scaled from ETH-gas via a
    `tokenRatio` (price(ETH)/price(MNT)). Source: OpenZeppelin "Mantle OP-Geth
    Audit". This is why fixed-gas-stipend value transfers are risky.
  - Mantle produces one block per transaction with variable block time
    (block production tied to tx arrival), so block.number is NOT a ~12s clock.
    Source: Mantle docs / explorer education.
  - Mantle V2 is built on the OP Stack (op-geth fork), so it inherits Optimism
    L2 semantics for block-level opcodes and randomness.
  - Mantle mainnet chainId = 5000, Sepolia testnet = 5003.

NOTE: Mantle is actively evolving (e.g. the Jan-2026 move toward Ethereum-blob
DA / ZK). Treat fee-mechanic rules as "flag for human review" and verify exact
current behaviour against https://docs.mantle.xyz before relying on a fix.
"""

from __future__ import annotations

import re
from typing import List

from .engine import Rule, top_level_arg_count

DOCS = "https://docs.mantle.xyz"
OZ_AUDIT = "https://www.openzeppelin.com/news/mantle-op-geth-audit"

# Known Ethereum-mainnet addresses that will NOT hold the same asset on Mantle.
KNOWN_L1_ADDRESSES = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH (Ethereum mainnet)",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC (Ethereum mainnet)",
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT (Ethereum mainnet)",
}


def _detect_native_transfer(src: str, blanked: str) -> List:
    """Flag `.transfer(x)` / `.send(x)` with a single argument (native value
    move using the 2300-gas stipend). Two-arg `.transfer(to, amount)` is an
    ERC-20 call and is intentionally ignored."""
    out = []
    for m in re.finditer(r"\.(transfer|send)\s*\(", blanked):
        open_paren = blanked.index("(", m.start())
        if top_level_arg_count(blanked, open_paren) == 1:
            out.append((m.start(), m.group(0)))
    return out


def _detect_l1_addresses(src: str, blanked: str) -> List:
    out = []
    for m in re.finditer(r"0x[0-9a-fA-F]{40}", blanked):
        if m.group(0).lower() in KNOWN_L1_ADDRESSES:
            out.append((m.start(), m.group(0)))
    return out


def build_rules() -> List[Rule]:
    return [
        Rule(
            id="MNT001",
            title="Native value transfer relies on the 2300-gas stipend",
            severity="HIGH",
            category="gas-token",
            message=(
                "`.transfer()`/`.send()` forward a fixed 2300-gas stipend. On Mantle the "
                "native token is MNT and gas is rescaled from ETH-gas via a tokenRatio, so "
                "the effective work a 2300-gas stipend buys can differ from L1 and may cause "
                "transfers to contract recipients to revert unexpectedly."
            ),
            recommendation=(
                "Use `(bool ok, ) = payable(to).call{value: amount}(\"\");` with an explicit "
                "success check and a reentrancy guard (checks-effects-interactions)."
            ),
            references=[OZ_AUDIT, DOCS],
            detector=_detect_native_transfer,
        ),
        Rule(
            id="MNT002",
            title="Hardcoded gas amount in external call",
            severity="HIGH",
            category="gas-token",
            message=(
                "A hardcoded `{gas: N}` value assumes L1 opcode gas costs. Under Mantle's "
                "MNT-denominated gas and tokenRatio scaling, a fixed gas budget can be too "
                "low (revert) or behave differently than intended."
            ),
            recommendation=(
                "Avoid hardcoding gas. Forward a safe portion of remaining gas, or make the "
                "limit configurable and validated against current Mantle gas behaviour."
            ),
            references=[OZ_AUDIT, DOCS],
            pattern=re.compile(r"\{\s*gas\s*:\s*\d[\d_]*\s*\}"),
        ),
        Rule(
            id="MNT003",
            title="block.number used in time/duration arithmetic",
            severity="MEDIUM",
            category="block-timing",
            message=(
                "Mantle generates one block per transaction with variable block time, so "
                "block.number does NOT advance on a fixed ~12s cadence. Treating block height "
                "as a clock (deadlines, vesting, reward-per-block) will be incorrect on Mantle."
            ),
            recommendation=(
                "Use `block.timestamp` for time-based logic. If you need per-block accounting, "
                "do not assume any fixed seconds-per-block relationship."
            ),
            references=[DOCS],
            # Multiplicative operators are the strong "blocks -> time" smell.
            # `block.number +/- 1` (previous-block idioms) are intentionally not flagged.
            pattern=re.compile(
                r"block\.number\s*[\*/]\s*\d"          # block.number * 12
                r"|\d\s*[\*/]\s*block\.number"         # 12 * block.number
            ),
        ),
        Rule(
            id="MNT004",
            title="Hardcoded blocks-per-time constant assumes 12s L1 blocks",
            severity="HIGH",
            category="block-timing",
            message=(
                "A constant like BLOCKS_PER_DAY = 7200 (or `* 12 seconds`) bakes in Ethereum's "
                "~12s block time. Mantle block production is per-transaction and variable, so "
                "this constant is meaningless and time-based logic built on it will drift."
            ),
            recommendation=(
                "Drive durations from `block.timestamp` (seconds), not from a blocks-per-period "
                "constant."
            ),
            references=[DOCS],
            pattern=re.compile(
                r"BLOCKS?_PER_(?:DAY|HOUR|WEEK|MONTH|YEAR|MINUTE)"
                r"|=\s*7200\b"
                r"|\*\s*12\s+seconds\b",
                re.IGNORECASE,
            ),
        ),
        Rule(
            id="MNT005",
            title="Hardcoded mainnet chainId (== 1)",
            severity="HIGH",
            category="chain-assumption",
            message=(
                "A `chainid == 1` check assumes Ethereum mainnet. Mantle mainnet is chainId "
                "5000 and Mantle Sepolia testnet is 5003, so this branch will never execute "
                "(or will wrongly reject) on Mantle."
            ),
            recommendation=(
                "Compare against the correct Mantle chainId (5000 / 5003), or parameterise "
                "supported chainIds instead of hardcoding 1."
            ),
            references=[DOCS],
            pattern=re.compile(r"(?:block\.)?chain[Ii]d\s*(?:==|!=)\s*1\b"),
        ),
        Rule(
            id="MNT006",
            title="Use of block.prevrandao / block.difficulty",
            severity="MEDIUM",
            category="randomness",
            message=(
                "On Mantle (OP-Stack L2 with a centralised sequencer) block.prevrandao / "
                "block.difficulty are not a meaningful or secure entropy source and differ "
                "from L1 semantics. Any randomness derived from them is predictable."
            ),
            recommendation=(
                "Use a dedicated randomness source (e.g. a VRF/oracle). Never derive "
                "security-relevant randomness from block-level values."
            ),
            references=[DOCS],
            pattern=re.compile(r"block\.(prevrandao|difficulty)\b"),
        ),
        Rule(
            id="MNT007",
            title="Use of blockhash()",
            severity="MEDIUM",
            category="randomness",
            message=(
                "blockhash() on an L2 only covers recent blocks and is influenced by the "
                "sequencer. It is unsafe as randomness and may not behave like L1 for "
                "historical-block assumptions."
            ),
            recommendation=(
                "Do not use blockhash for randomness or long-range history. Use an oracle/VRF "
                "where unpredictability matters."
            ),
            references=[DOCS],
            pattern=re.compile(r"\bblockhash\s*\("),
        ),
        Rule(
            id="MNT008",
            title="tx.gasprice / block.basefee dependency",
            severity="MEDIUM",
            category="fee-mechanics",
            message=(
                "Mantle's fee model (MNT gas, tokenRatio, L1-DA fee component, operator fee) "
                "differs from L1 EIP-1559. Logic that reads tx.gasprice or block.basefee — "
                "e.g. gas refunds, fee reimbursement — can compute wrong values."
            ),
            recommendation=(
                "Re-derive any fee/refund math against Mantle's current fee model and verify "
                "with on-chain measurements before relying on these values."
            ),
            references=[OZ_AUDIT, DOCS],
            pattern=re.compile(r"\btx\.gasprice\b|\bblock\.basefee\b"),
        ),
        Rule(
            id="MNT009",
            title="tx.origin used for authorization",
            severity="MEDIUM",
            category="auth",
            message=(
                "tx.origin authorization is unsafe in general, and Mantle supports native "
                "meta-transactions, which can further break the assumption that tx.origin is "
                "the intended caller."
            ),
            recommendation=(
                "Authorize against `msg.sender`. If you need meta-tx support, adopt a proper "
                "forwarder/permit pattern."
            ),
            references=[OZ_AUDIT, DOCS],
            pattern=re.compile(r"\btx\.origin\b"),
        ),
        Rule(
            id="MNT010",
            title="address(this).balance is denominated in MNT, not ETH",
            severity="INFO",
            category="gas-token",
            message=(
                "Native balance on Mantle is MNT, not ETH. If your accounting, event labels, "
                "or price math assume the native balance is ETH, values and UX will be wrong."
            ),
            recommendation=(
                "Confirm native-balance handling treats the unit as MNT, and that any "
                "USD/ETH conversion uses the right asset."
            ),
            references=[DOCS],
            pattern=re.compile(r"address\s*\(\s*this\s*\)\s*\.\s*balance"),
        ),
        Rule(
            id="MNT011",
            title="Hardcoded Ethereum-mainnet contract address",
            severity="INFO",
            category="chain-assumption",
            message=(
                "This is a well-known Ethereum-mainnet token/contract address. The equivalent "
                "asset on Mantle lives at a different address; the hardcoded value will point "
                "at nothing (or the wrong contract) on Mantle."
            ),
            recommendation=(
                "Replace with the Mantle deployment address (check the Mantle bridge/token "
                "list) or inject the address via constructor/config per network."
            ),
            references=[DOCS],
            detector=_detect_l1_addresses,
        ),
    ]

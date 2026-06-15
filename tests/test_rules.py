"""Reproducible rule tests. Run with: python3 -m pytest -q  (or python3 tests/test_rules.py)

These tests double as the 'verifiability' evidence for the tool: each Mantle rule
has a positive case it must catch and the clean contract must stay silent.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mantle_lint.engine import scan
from mantle_lint.report import _ascii_safe
from mantle_lint.rules import build_rules

RULES = build_rules()
HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(os.path.dirname(HERE), "examples")


def _ids(src):
    return {f.rule_id for f in scan("t.sol", src, RULES)}


def test_clean_contract_is_silent():
    with open(os.path.join(EX, "CleanStaking.sol")) as fh:
        assert scan("CleanStaking.sol", fh.read(), RULES) == []


def test_vulnerable_contract_triggers_core_rules():
    with open(os.path.join(EX, "VulnerableStaking.sol")) as fh:
        ids = {f.rule_id for f in scan("v.sol", fh.read(), RULES)}
    for expected in {"MNT001", "MNT002", "MNT004", "MNT005", "MNT006", "MNT011"}:
        assert expected in ids, f"missing {expected}"


def test_native_transfer_flagged_but_erc20_transfer_ignored():
    ids = _ids("contract C{ function f(address t,uint a) external { "
               "payable(t).transfer(a); token.transfer(t, a); } }")
    assert "MNT001" in ids  # native single-arg transfer
    # ERC-20 two-arg transfer must NOT be the cause of a second MNT001 on a new line
    assert sum(1 for f in scan('x.sol',
        'contract C{function f(address t,uint a) external {token.transfer(t,a);}}',
        RULES) if f.rule_id == "MNT001") == 0


def test_block_number_offset_not_flagged():
    # previous-block idiom is benign and must not trigger MNT003
    assert "MNT003" not in _ids("contract C{function f() external view returns(bytes32){"
                                "return blockhash(block.number - 1);}}")


def test_comment_and_string_are_ignored():
    src = ('contract C{ // tx.origin here is a comment\n'
           'string s = "block.chainid == 1"; }')
    assert _ids(src) == set()


class _FakeStdout:
    def __init__(self, encoding):
        self.encoding = encoding


def test_terminal_output_downgrades_glyphs_on_cp1252():
    # A default Windows console (cp1252) can't encode -> / em-dash; rendering
    # must downgrade to ASCII instead of crashing with UnicodeEncodeError.
    saved = sys.stdout
    sys.stdout = _FakeStdout("cp1252")
    try:
        out = _ascii_safe("arrow → and dash —")
    finally:
        sys.stdout = saved
    assert out == "arrow -> and dash -"
    out.encode("cp1252")  # must not raise


def test_terminal_output_preserves_glyphs_on_utf8():
    saved = sys.stdout
    sys.stdout = _FakeStdout("utf-8")
    try:
        assert _ascii_safe("arrow → and dash —") == "arrow → and dash —"
    finally:
        sys.stdout = saved


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")

"""Reproducible rule tests. Run with: python3 -m pytest -q  (or python3 tests/test_rules.py)

These tests double as the 'verifiability' evidence for the tool: each Mantle rule
has a positive case it must catch and the clean contract must stay silent.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mantle_lint import ai
from mantle_lint.engine import Finding, scan
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


# --- AI triage layer (ai.py) -------------------------------------------------
# All tests are OFFLINE: the HTTP boundary (ai._call_endpoint) is monkeypatched,
# so no network is ever touched.

def _finding(rule_id="MNT001", line=2, file="t.sol"):
    return Finding(rule_id=rule_id, title="t", severity="HIGH", category="c",
                   file=file, line=line, col=1, snippet="x", message="m",
                   recommendation="r")


def _cfg(cache_dir):
    return ai.AiConfig(base_url="http://host:8000/v1", model="test-model",
                       cache_dir=cache_dir)


def test_ai_config_fails_clearly_without_base_url():
    saved = dict(os.environ)
    for k in ("MANTLE_LINT_AI_BASE_URL", "MANTLE_LINT_AI_MODEL"):
        os.environ.pop(k, None)
    try:
        raised = False
        try:
            ai.load_config()
        except ai.AiConfigError:
            raised = True
        assert raised, "load_config must raise when base_url is unset"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_ai_code_window_marks_finding_line():
    win = ai.code_window("a\nb\nc\nd\ne", line=3, radius=1)
    assert ">>" in win and "c" in win
    assert win.count(">>") == 1


def test_ai_validate_strict_schema():
    ok = {"exploitability": "MED", "reason": "x\nsecond", "patch": "--- a\n+++ b"}
    ann = ai._validate(ok)
    assert ann is not None and ann.exploitability == "medium"
    assert ann.reason == "x"  # only first line kept
    assert ai._validate({"exploitability": "spicy", "reason": "x", "patch": "p"}) is None
    assert ai._validate({"exploitability": "high", "patch": "p"}) is None  # missing reason
    assert ai._validate({"exploitability": "high", "reason": "x", "patch": "  "}) is None
    assert ai._validate("not a dict") is None


def test_ai_parse_content_strips_markdown_fences():
    fenced = '```json\n{"exploitability":"low","reason":"r","patch":"--- a"}\n```'
    ann = ai._parse_content(fenced)
    assert ann is not None and ann.exploitability == "low"


def test_ai_triage_annotates_and_caches():
    tmp = tempfile.mkdtemp()
    calls = []
    good = '{"exploitability":"high","reason":"reachable by anyone","patch":"--- a\\n+++ b"}'

    def fake_call(config, finding, window):
        calls.append(window)
        return good

    saved = ai._call_endpoint
    ai._call_endpoint = fake_call
    try:
        f1 = _finding()
        ai.triage([f1], {"t.sol": "x\ny\nz"}, _cfg(tmp), warn=lambda m: None)
        assert f1.ai_exploitability == "high"
        assert f1.ai_reason == "reachable by anyone"
        assert f1.ai_patch.startswith("--- a")
        assert len(calls) == 1
        # Second run on an equivalent finding must hit the disk cache, not the endpoint.
        f2 = _finding()
        ai.triage([f2], {"t.sol": "x\ny\nz"}, _cfg(tmp), warn=lambda m: None)
        assert f2.ai_exploitability == "high"
        assert len(calls) == 1, "cache miss: endpoint was called again"
    finally:
        ai._call_endpoint = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_ai_triage_skips_unusable_output():
    tmp = tempfile.mkdtemp()

    def fake_call(config, finding, window):
        return "this is not json"

    saved = ai._call_endpoint
    ai._call_endpoint = fake_call
    warned = []
    try:
        f = _finding()
        ai.triage([f], {"t.sol": "x\ny\nz"}, _cfg(tmp), warn=warned.append)
        assert f.ai_exploitability is None  # deterministic finding untouched
        assert any("unusable" in w for w in warned)
    finally:
        ai._call_endpoint = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_ai_triage_degrades_gracefully_when_endpoint_down():
    import urllib.error
    tmp = tempfile.mkdtemp()

    def fake_call(config, finding, window):
        raise urllib.error.URLError("connection refused")

    saved = ai._call_endpoint
    ai._call_endpoint = fake_call
    warned = []
    try:
        f = _finding()
        # Must not raise.
        ai.triage([f], {"t.sol": "x\ny\nz"}, _cfg(tmp), warn=warned.append)
        assert f.ai_exploitability is None
        assert any("unreachable" in w for w in warned)
    finally:
        ai._call_endpoint = saved
        shutil.rmtree(tmp, ignore_errors=True)


# --- --ai wiring in cli.py (TASK 2) ------------------------------------------

def test_ai_off_output_shape_is_unchanged():
    """AI-off rendering must introduce no AI markers in any format."""
    from mantle_lint.report import render_terminal, render_json, render_sarif
    with open(os.path.join(EX, "VulnerableStaking.sol")) as fh:
        findings = scan("VulnerableStaking.sol", fh.read(), RULES)
    assert "AI exploitability" not in render_terminal(findings, color=False)
    j = render_json(findings)
    assert "aiExploitability" not in j and "aiPatch" not in j
    assert "aiExploitability" not in render_sarif(findings)


class _FakeResp:
    def __init__(self, body):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_cli_ai_flag_enriches_findings_with_mocked_http():
    """Full `--ai` cli run, OFFLINE: monkeypatch urllib.request.urlopen so the
    real serialization path runs but no network is touched."""
    import io
    import json as _json
    import urllib.request
    from mantle_lint import cli

    content = _json.dumps({
        "exploitability": "high",
        "reason": "external call recipient can revert under MNT gas scaling",
        "patch": "--- a/VulnerableStaking.sol\n+++ b/VulnerableStaking.sol\n@@\n- old\n+ new",
    })
    http_body = _json.dumps({"choices": [{"message": {"content": content}}]})

    def fake_urlopen(req, timeout=None):
        return _FakeResp(http_body)

    tmp = tempfile.mkdtemp()
    saved_env = dict(os.environ)
    saved_urlopen = urllib.request.urlopen
    saved_stdout = sys.stdout
    urllib.request.urlopen = fake_urlopen
    os.environ["MANTLE_LINT_AI_BASE_URL"] = "http://host:8000/v1"
    os.environ["MANTLE_LINT_AI_MODEL"] = "test-model"
    os.environ["MANTLE_LINT_AI_CACHE_DIR"] = tmp
    sys.stdout = io.StringIO()
    try:
        rc = cli.run([os.path.join(EX, "VulnerableStaking.sol"), "--ai", "--no-color"])
        out = sys.stdout.getvalue()
    finally:
        sys.stdout = saved_stdout
        urllib.request.urlopen = saved_urlopen
        os.environ.clear()
        os.environ.update(saved_env)
        shutil.rmtree(tmp, ignore_errors=True)
    assert rc == 1  # HIGH findings present -> gate still fails
    assert "AI exploitability: high" in out
    assert "AI patch (suggestion" in out
    assert "+++ b/VulnerableStaking.sol" in out


def test_cli_ai_flag_fails_clearly_without_base_url():
    import io
    from mantle_lint import cli
    saved_env = dict(os.environ)
    saved_stderr = sys.stderr
    for k in ("MANTLE_LINT_AI_BASE_URL", "MANTLE_LINT_AI_MODEL"):
        os.environ.pop(k, None)
    sys.stderr = io.StringIO()
    try:
        rc = cli.run([os.path.join(EX, "VulnerableStaking.sol"), "--ai"])
        err = sys.stderr.getvalue()
    finally:
        sys.stderr = saved_stderr
        os.environ.clear()
        os.environ.update(saved_env)
    assert rc == 2
    assert "MANTLE_LINT_AI_BASE_URL" in err


# --- benchmark integration (benchmarks.py + --benchmarks) --------------------

BENCH_RESULTS = os.path.join(os.path.dirname(HERE), "benchmarks", "results.json")


def test_benchmarks_loader_reads_results_json():
    from mantle_lint import benchmarks
    ann = benchmarks.load_annotations(BENCH_RESULTS)
    assert "MNT001" in ann
    note = ann["MNT001"]["note"]
    assert "REVERTED" in note and "Mantle Sepolia" in note
    assert ann["MNT001"]["links"]  # real explorer links present


def test_benchmarks_attach_only_matching_rule():
    from mantle_lint import benchmarks
    with open(os.path.join(EX, "VulnerableStaking.sol")) as fh:
        findings = scan("v.sol", fh.read(), RULES)
    benchmarks.attach(findings, benchmarks.load_annotations(BENCH_RESULTS))
    mnt001 = [f for f in findings if f.rule_id == "MNT001"]
    assert mnt001 and all(f.benchmark for f in mnt001)
    assert all(f.benchmark is None for f in findings if f.rule_id != "MNT001")


def test_benchmark_off_shape_unchanged():
    from mantle_lint.report import render_terminal, render_json
    with open(os.path.join(EX, "VulnerableStaking.sol")) as fh:
        findings = scan("v.sol", fh.read(), RULES)
    assert "measured:" not in render_terminal(findings, color=False)
    assert '"benchmark"' not in render_json(findings)


# --- gas-regression report (benchmarks/gas_regression.py) --------------------

def _gas_mod():
    bench_dir = os.path.join(os.path.dirname(HERE), "benchmarks")
    if bench_dir not in sys.path:
        sys.path.insert(0, bench_dir)
    import gas_regression
    return gas_regression


def test_gas_regression_no_regression_when_equal():
    g = _gas_mod()
    results = g._load(BENCH_RESULTS)
    baseline = g.snapshot_from_results(results)  # baseline == current => zero deltas
    comment = g.build_comment(results, baseline)
    assert g.MARKER in comment
    assert "Mantle gas report" in comment and "MNT001" in comment
    assert "No gas regression" in comment
    assert "+0" in comment
    # behavioural call-outs from the real on-chain run
    assert "reverts" in comment and "succeeds" in comment


def test_gas_regression_flags_increase():
    g = _gas_mod()
    results = g._load(BENCH_RESULTS)
    baseline = g.snapshot_from_results(results)
    # pretend the baseline was lower for one scenario -> current is a regression
    name = next(iter(baseline["scenarios"]))
    baseline["scenarios"][name]["gasUsed"] -= 1000
    comment = g.build_comment(results, baseline)
    assert "gas increased vs baseline" in comment
    assert f"`{name}` (+1000)" in comment


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")

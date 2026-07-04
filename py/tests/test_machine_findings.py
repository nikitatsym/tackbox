"""Machine-output plumbing: every engine emits the internal {file, line, rule}
contract and the CLI parses it into located Findings.

Unit tests pin the parsers; per-engine integration fixtures plant one violation
on a known line and assert its parsed location. Fixture sources are inline (not
on-disk trees) so tackbox self-lint never sees them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tackbox.engines import (
    Finding,
    active_engines,
    dispatch,
    erclint_located_findings,
    located_findings,
    parse_machine_findings,
    run_engines,
)

TACKBOX_ROOT = Path(__file__).resolve().parents[2]


# ---- unit: parsers -------------------------------------------------------


def test_parse_machine_findings_ndjson():
    out = '{"file": "a.py", "line": 4, "rule": "python-swallowed-exception"}\n'
    assert parse_machine_findings(out) == [
        Finding(rule="python-swallowed-exception", file="a.py", line=4)
    ]


def test_parse_machine_findings_missing_location_is_none():
    # location-unknown: reported, not dropped.
    out = '{"rule": "opengrep-json-unparseable"}\n'
    assert parse_machine_findings(out) == [
        Finding(rule="opengrep-json-unparseable", file=None, line=None)
    ]


def test_parse_machine_findings_skips_blank_lines():
    assert parse_machine_findings("\n  \n") == []


def test_erclint_located_findings_relativizes_posn():
    raw = '{"pkg": {"errcheck": [{"posn": "/repo/pkg/a.go:7:2", "message": "ERC001: x"}]}}'
    assert erclint_located_findings(raw, Path("/repo")) == [
        Finding(rule="errcheck", file="pkg/a.go", line=7)
    ]


def test_erclint_malformed_posn_is_location_unknown():
    raw = '{"pkg": {"errcheck": [{"posn": "weird", "message": "x"}]}}'
    assert erclint_located_findings(raw, Path("/repo")) == [
        Finding(rule="errcheck", file=None, line=None)
    ]


def test_located_findings_dispatches_erclint_vs_machine():
    erc = '{"p": {"errcheck": [{"posn": "/r/a.go:3:1", "message": "m"}]}}'
    assert located_findings("erclint", erc, Path("/r")) == [
        Finding(rule="errcheck", file="a.go", line=3)
    ]
    nd = '{"file": "b.js", "line": 9, "rule": "tackbox/no-swallow-catch"}\n'
    assert located_findings("tackbox-eslint", nd, Path("/r")) == [
        Finding(rule="tackbox/no-swallow-catch", file="b.js", line=9)
    ]


# ---- integration: each engine reports the planted line -------------------

GO_MOD = "module mf\n\ngo 1.24\n"
# ERC001 fires on the `if err != nil` line (line 7).
GO_BAD = (
    'package pkg\n\nimport "errors"\n\n'
    'func F() error {\n\terr := errors.New("x")\n\tif err != nil {\n'
    '\t\t_ = "swallow"\n\t}\n\treturn errors.New("noop")\n}\n'
)
# python-swallowed-exception matches the try block (line 2).
PY_BAD = "def h():\n    try:\n        work()\n    except ValueError as e:\n        pass\n"
# no-swallow-catch fires on line 1.
JS_BAD = "try { f() } catch (e) {}\n"
# no-non-ascii fires on the em-dash line (line 3).
MD_BAD = "# T\n\nem dash — here\n"


@pytest.fixture(scope="module")
def machine_findings(tmp_path_factory):
    repo = tmp_path_factory.mktemp("mfrepo")
    (repo / "go.mod").write_text(GO_MOD)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "bad.go").write_text(GO_BAD)
    (repo / "bad.py").write_text(PY_BAD)
    (repo / "bad.js").write_text(JS_BAD)
    (repo / "bad.md").write_text(MD_BAD)
    files = ["pkg/bad.go", "bad.py", "bad.js", "bad.md"]
    plan = dispatch(files, active_engines())
    results = run_engines(plan, repo, TACKBOX_ROOT, machine=True)
    return [f for r in results for f in located_findings(r.engine_id, r.stdout, repo)]


def _hit(findings, rule_substr, file):
    return [f for f in findings if rule_substr in f.rule and f.file == file]


def test_erclint_machine_location(machine_findings):
    hits = _hit(machine_findings, "errcheck", "pkg/bad.go")
    assert hits and hits[0].line == 7, machine_findings


def test_opengrep_machine_location(machine_findings):
    hits = _hit(machine_findings, "python-swallowed-exception", "bad.py")
    assert hits and hits[0].line == 2, machine_findings


def test_eslint_machine_location(machine_findings):
    hits = _hit(machine_findings, "no-swallow-catch", "bad.js")
    assert hits and hits[0].line == 1, machine_findings


def test_mdlint_machine_location(machine_findings):
    hits = _hit(machine_findings, "MD-ASCII", "bad.md")
    assert hits and hits[0].line == 3, machine_findings

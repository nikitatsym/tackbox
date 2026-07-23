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


def test_parse_machine_findings_carries_message():
    out = '{"file": "a.py", "line": 4, "rule": "r", "message": "why it is wrong"}\n'
    assert parse_machine_findings(out) == [
        Finding(rule="r", file="a.py", line=4, message="why it is wrong")
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
        Finding(rule="errcheck", file="pkg/a.go", line=7, message="ERC001: x")
    ]


def test_erclint_malformed_posn_is_location_unknown():
    raw = '{"pkg": {"errcheck": [{"posn": "weird", "message": "x"}]}}'
    assert erclint_located_findings(raw, Path("/repo")) == [
        Finding(rule="errcheck", file=None, line=None, message="x")
    ]


def test_located_findings_dispatches_erclint_vs_machine():
    erc = '{"p": {"errcheck": [{"posn": "/r/a.go:3:1", "message": "m"}]}}'
    assert located_findings("erclint", erc, Path("/r")) == [
        Finding(rule="errcheck", file="a.go", line=3, message="m")
    ]
    nd = '{"file": "b.js", "line": 9, "rule": "tackbox/no-swallow-catch"}\n'
    assert located_findings("tackbox-eslint", nd, Path("/r")) == [
        Finding(rule="tackbox/no-swallow-catch", file="b.js", line=9)
    ]


def test_located_findings_javalint_keeps_repo_relative_file():
    """javalint emits repo-relative file keys (unlike erclint's absolute posn),
    so its located file must be the key verbatim - no relpath. The repo_root here
    is deliberately unrelated: an erclint-style relpath would mangle the path."""
    jl = (
        '{\n  "java/Foo.java": {\n    "JV001": [\n'
        '      {"posn": "java/Foo.java:5:9", "end": "java/Foo.java:5:9", "message": "m"}\n'
        "    ]\n  }\n}\n"
    )
    assert located_findings("javalint", jl, Path("/some/unrelated/root")) == [
        Finding(rule="JV001", file="java/Foo.java", line=5, message="m")
    ]


def test_pyrules_located_findings_strips_rule_id_echo():
    out = "a.py:4:9: TBX001 python-swallowed-exception: `except` block has no `raise`\n"
    assert located_findings("pyrules", out, Path("/r")) == [
        Finding(
            rule="python-swallowed-exception",
            file="a.py",
            line=4,
            message="`except` block has no `raise`",
        )
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
# declared-chars fires on the em-dash line (line 4) under a chars=ascii carrier.
MD_BAD = "<!-- tackbox: chars=ascii -->\n# T\n\nem dash \u2014 here\n"


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
    assert hits[0].message and "ERC001" in hits[0].message, hits


def test_pyrules_machine_location(machine_findings):
    hits = _hit(machine_findings, "python-swallowed-exception", "bad.py")
    assert hits and hits[0].line == 2, machine_findings
    assert hits[0].message and not hits[0].message.startswith(hits[0].rule), hits


def test_eslint_machine_location(machine_findings):
    hits = _hit(machine_findings, "no-swallow-catch", "bad.js")
    assert hits and hits[0].line == 1, machine_findings
    assert hits[0].message and "every catch path" in hits[0].message, hits


def test_mdlint_machine_location(machine_findings):
    hits = _hit(machine_findings, "MD-CHARS", "bad.md")
    assert hits and hits[0].line == 4, machine_findings
    assert hits[0].message, hits

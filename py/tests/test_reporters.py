"""Parsing and path-validation of `.tackbox-reporters` (pure CLI layer).

Symbol existence (dead `file#function`) is validated by the resolving
engine, so it lives in the end-to-end fixture (test_cli_fixture.py), not
here.
"""

from __future__ import annotations

import pytest

from tackbox import reporters
from tackbox.reporters import Declaration, ReportersError


def test_parse_valid():
    text = "src/report.js#reportErr: wrapper around sentry\nsvc/rep.go#Report: local sink\n"
    assert reporters.parse(text) == [
        Declaration("src/report.js", "reportErr", "wrapper around sentry"),
        Declaration("svc/rep.go", "Report", "local sink"),
    ]


def test_parse_blank_lines_ignored():
    assert reporters.parse("\n  \nsrc/a.ts#f: r\n\n") == [
        Declaration("src/a.ts", "f", "r")
    ]


def test_parse_garbage_line_reports_line_number():
    with pytest.raises(ReportersError) as ei:
        reporters.parse("src/a.ts#f: ok\nnonsense line\n")
    assert ":2:" in str(ei.value)


def test_parse_comment_line_rejected():
    # No comments in the file; a leading-# line has an empty file part.
    with pytest.raises(ReportersError) as ei:
        reporters.parse("# not a comment\n")
    assert ":1:" in str(ei.value)


@pytest.mark.parametrize("text", ["src/a.ts#f:\n", "src/a.ts#f: \n", "src/a.ts#: r\n"])
def test_parse_empty_field_rejected(text):
    with pytest.raises(ReportersError):
        reporters.parse(text)


def test_parse_missing_hash():
    with pytest.raises(ReportersError):
        reporters.parse("src/a.ts: r\n")


def test_validate_missing_file(tmp_path):
    with pytest.raises(ReportersError, match="no such file"):
        reporters.validate_paths([Declaration("nope.go", "F", "r")], tmp_path)


def test_validate_path_escapes_repo(tmp_path):
    (tmp_path / "inside.go").write_text("package x\n")
    with pytest.raises(ReportersError, match="escapes repo"):
        reporters.validate_paths([Declaration("../outside.go", "F", "r")], tmp_path)


def test_validate_unsupported_extension(tmp_path):
    (tmp_path / "a.rb").write_text("")
    with pytest.raises(ReportersError, match="unsupported language"):
        reporters.validate_paths([Declaration("a.rb", "F", "r")], tmp_path)


def test_validate_java_reporter_supported(tmp_path):
    # java sinks resolve via javalint (tier-2 file#Class.method). .java must stay
    # a known decl extension after the opengrep->javalint cutover, or a java
    # reporter declaration would be rejected at the CLI boundary before the
    # engine ever runs. Symbol existence is javalint's job, not validate_paths'.
    (tmp_path / "Rep.java").write_text(
        "class Rep { static void report(Throwable e) {} }\n"
    )
    reporters.validate_paths(
        [Declaration("Rep.java", "Rep.report", "java sink")], tmp_path
    )


def test_load_absent_file_no_declarations(tmp_path):
    assert reporters.load(tmp_path) == []


def test_load_parses_and_validates(tmp_path):
    (tmp_path / "rep.go").write_text("package x\nfunc Report() {}\n")
    (tmp_path / ".tackbox-reporters").write_text("rep.go#Report: sink\n")
    assert reporters.load(tmp_path) == [Declaration("rep.go", "Report", "sink")]


def test_pairs():
    decls = [
        Declaration("a.go", "F", "r"),
        Declaration("b.ts", "g", "r2", kind="usage"),
    ]
    assert reporters.pairs(decls) == (
        ("a.go", "F", "capture"),
        ("b.ts", "g", "usage"),
    )


def test_parse_usage_kind():
    assert reporters.parse("cli.go#usage [usage]: diagnostic exit\n") == [
        Declaration("cli.go", "usage", "diagnostic exit", kind="usage")
    ]


def test_parse_unknown_kind_rejected():
    with pytest.raises(ReportersError, match=r"unknown sink kind \[fatal\]"):
        reporters.parse("cli.go#die [fatal]: nope\n")

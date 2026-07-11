"""Unit tests for engine dispatch and helpers.

End-to-end wiring (subprocess run against real binaries + fixture repo)
lives in test_cli_fixture.py; here the tests are pure logic on the
public surface of tackbox.engines.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tackbox.engines as engines
from tackbox.engines import (
    DEV_ENGINES,
    EngineSpec,
    dispatch,
    normalize_exit_code,
    parse_erclint_findings,
)


def _spec(id_: str, exts, package_mode=False) -> EngineSpec:
    return EngineSpec(
        id=id_,
        extensions=frozenset(exts),
        build_argv=lambda repo, root, args: [id_, *args],
        package_mode=package_mode,
    )


# -- normalize_exit_code ---------------------------------------------------


def test_normalize_zero_stays_zero():
    assert normalize_exit_code(0) == 0


def test_normalize_positive_passthrough():
    assert normalize_exit_code(1) == 1
    assert normalize_exit_code(2) == 2


def test_normalize_signal_maps_to_128_plus_sig():
    # Python subprocess returns `-signal` for signal-terminated children.
    assert normalize_exit_code(-9) == 137
    assert normalize_exit_code(-15) == 143


# -- dispatch --------------------------------------------------------------


def test_dispatch_by_extension_only_matching_engines_run():
    js = _spec("js", [".js"])
    md = _spec("md", [".md"])
    plan = dispatch(["a.md"], [js, md])
    assert [(spec.id, args) for spec, args in plan] == [("md", ["a.md"])]


def test_dispatch_multiple_engines_can_share_files():
    a = _spec("a", [".go"])
    b = _spec("b", [".go"])
    plan = dispatch(["x.go"], [a, b])
    assert [spec.id for spec, _ in plan] == ["a", "b"]
    assert all(args == ["x.go"] for _, args in plan)


def test_dispatch_preserves_engine_order():
    a = _spec("erclint", [".go"], package_mode=True)
    b = _spec("erclint-opengrep", [".go"])
    c = _spec("tackbox-eslint", [".js"])
    plan = dispatch(["pkg/a.go", "src/b.js"], [a, b, c])
    assert [spec.id for spec, _ in plan] == [
        "erclint",
        "erclint-opengrep",
        "tackbox-eslint",
    ]


def test_dispatch_package_mode_collapses_go_files_to_dirs():
    spec = _spec("erclint", [".go"], package_mode=True)
    plan = dispatch(["pkg/a.go", "pkg/b.go", "other/c.go"], [spec])
    assert [(s.id, args) for s, args in plan] == [
        ("erclint", ["other", "pkg"]),
    ]


def test_dispatch_no_matching_files_drops_engine():
    spec = _spec("md", [".md"])
    assert dispatch(["a.go", "b.js"], [spec]) == []


def test_dispatch_extension_matches_last_dot_only():
    spec = _spec("js", [".js"])
    # a.min.js still matches; a.js.bak does not.
    plan = dispatch(["a.min.js", "a.js.bak"], [spec])
    assert [(s.id, args) for s, args in plan] == [("js", ["a.min.js"])]


def test_dispatch_files_without_extension_ignored():
    spec = _spec("md", [".md"])
    assert dispatch(["Makefile", "README"], [spec]) == []


def test_dispatch_path_filter_drops_matching_files():
    # Simulate a Go-testdata filter for a Go-only engine.
    spec = EngineSpec(
        id="go",
        extensions=frozenset([".go"]),
        build_argv=lambda repo, root, args: ["go", *args],
        path_filter=lambda p: "testdata" not in p.split("/"),
    )
    plan = dispatch(
        ["pkg/a.go", "pkg/testdata/src/b.go"], [spec]
    )
    assert plan == [(spec, ["pkg/a.go"])]


def test_dispatch_dev_engines_erclint_skips_go_testdata():
    erclint = next(e for e in DEV_ENGINES if e.id == "erclint")
    plan = dispatch(
        [
            "go/pkg/real.go",
            "go/analyzers/errcheck/testdata/src/errcheck/bad.go",
        ],
        [erclint],
    )
    # `testdata/src/...` path is filtered; only the real package remains.
    assert plan == [(erclint, ["go/pkg"])]


def test_dispatch_dev_engines_opengrep_skips_go_testdata_but_not_other_langs():
    opengrep = next(e for e in DEV_ENGINES if e.id == "erclint-opengrep")
    plan = dispatch(
        [
            "src/app.go",
            "src/testdata/case.go",
            "src/testdata/case.py",
        ],
        [opengrep],
    )
    # Go testdata dropped; Python testdata kept - the convention is Go-only.
    assert plan == [(opengrep, ["src/app.go", "src/testdata/case.py"])]


# -- parse_erclint_findings ------------------------------------------------


def test_parse_erclint_findings_empty_string():
    assert parse_erclint_findings("") == []


def test_parse_erclint_findings_empty_object():
    assert parse_erclint_findings("{}") == []


def test_parse_erclint_findings_single_finding():
    payload = """
    {
        "fixture/pkga": {
            "errcheck": [
                {"posn": "pkga/a.go:7:2", "end": "pkga/a.go:7:2", "message": "ERC001: ..."}
            ]
        }
    }
    """
    findings = parse_erclint_findings(payload)
    assert findings == [
        {
            "pkg": "fixture/pkga",
            "analyzer": "errcheck",
            "posn": "pkga/a.go:7:2",
            "end": "pkga/a.go:7:2",
            "message": "ERC001: ...",
        }
    ]


def test_parse_erclint_findings_multiple_packages_and_analyzers():
    payload = """
    {
        "pkga": {
            "errcheck": [{"posn": "a", "end": "a", "message": "m1"}],
            "returnnil": [{"posn": "b", "end": "b", "message": "m2"}]
        },
        "pkgb": {
            "errcheck": [{"posn": "c", "end": "c", "message": "m3"}]
        }
    }
    """
    findings = parse_erclint_findings(payload)
    assert {(f["pkg"], f["analyzer"], f["message"]) for f in findings} == {
        ("pkga", "errcheck", "m1"),
        ("pkga", "returnnil", "m2"),
        ("pkgb", "errcheck", "m3"),
    }


def test_parse_erclint_findings_analyzer_error_bubbles():
    payload = '{"pkga": {"errcheck": {"error": "boom"}}}'
    with pytest.raises(ValueError):
        parse_erclint_findings(payload)


def test_parse_erclint_findings_parses_javalint_json():
    """Regression: javalint emits erclint-shaped JSON (JsonWriter) so the CLI
    parses both engines through the one parse_erclint_findings path. The outer
    key is the repo-relative file, the inner key the JVNNN rule id."""
    # Verbatim JsonWriter shape (pretty-printed, file key = path as passed).
    payload = (
        "{\n"
        '  "java/Foo.java": {\n'
        '    "JV001": [\n'
        '      {"posn": "java/Foo.java:2:40", "end": "java/Foo.java:2:40", "message": "JV001: ..."}\n'
        "    ],\n"
        '    "JV002": [\n'
        '      {"posn": "java/Foo.java:3:67", "end": "java/Foo.java:3:67", "message": "JV002: ..."}\n'
        "    ]\n"
        "  }\n"
        "}\n"
    )
    findings = parse_erclint_findings(payload)
    assert {(f["pkg"], f["analyzer"], f["posn"]) for f in findings} == {
        ("java/Foo.java", "JV001", "java/Foo.java:2:40"),
        ("java/Foo.java", "JV002", "java/Foo.java:3:67"),
    }


# -- DEV_ENGINES registry (shape checks, not behavior) --------------------


def test_dev_engines_registry_order_locked():
    assert [e.id for e in DEV_ENGINES] == [
        "erclint",
        "erclint-opengrep",
        "tackbox-jscpd",
        "javalint",
        "tackbox-eslint",
        "tackbox-mdlint",
        "pyrules",
    ]


def test_pyrules_invocation_neutralizes_ambient_channels_structurally():
    # --select=TBX gates out a consumer's own flake8 plugin - too costly to pin
    # behaviorally; --isolated / --disable-noqa are pinned in the hardening test.
    pyrules = next(e for e in DEV_ENGINES if e.id == "pyrules")
    argv = pyrules.build_argv(None, None, ["a.py"], ())
    assert "flake8" in argv
    assert "--isolated" in argv
    assert "--disable-noqa" in argv
    assert "--select=TBX" in argv
    assert argv[-1] == "a.py"


def test_dev_engines_erclint_is_package_mode():
    erclint = next(e for e in DEV_ENGINES if e.id == "erclint")
    assert erclint.package_mode is True
    # Other engines run per-file.
    for e in DEV_ENGINES:
        if e.id != "erclint":
            assert e.package_mode is False


def test_dev_engines_opengrep_covers_multi_language():
    og = next(e for e in DEV_ENGINES if e.id == "erclint-opengrep")
    assert ".go" in og.extensions
    assert ".py" in og.extensions
    assert ".ts" in og.extensions
    # java moved to the javalint engine; svelte has no opengrep parser.
    assert ".java" not in og.extensions
    assert ".svelte" not in og.extensions


def test_dispatch_dev_engines_routes_java_to_javalint():
    # Java goes to javalint (not opengrep) and to tackbox-jscpd (duplication
    # spans every language); dispatch order follows the registry.
    javalint = next(e for e in DEV_ENGINES if e.id == "javalint")
    jscpd = next(e for e in DEV_ENGINES if e.id == "tackbox-jscpd")
    plan = dispatch(["src/Main.java"], DEV_ENGINES)
    assert plan == [(jscpd, ["src/Main.java"]), (javalint, ["src/Main.java"])]


def test_dev_engines_javalint_extension_is_only_java():
    jl = next(e for e in DEV_ENGINES if e.id == "javalint")
    assert jl.extensions == frozenset([".java"])
    # per-file (not package_mode) and erclint-shaped JSON, not machine NDJSON.
    assert jl.package_mode is False
    assert jl.machine_flag is False


def test_hermetic_javalint_argv_uses_system_java_and_thin_jar():
    jl = next(e for e in engines.HERMETIC_ENGINES if e.id == "javalint")
    argv = jl.build_argv(
        Path("/repo"), Path("/tb"), ["a.java"], (("Rep.java", "Rep.report", "capture"),)
    )
    assert argv[:3] == [
        "java", "-jar", str(engines._TACKBOX_PKG_ROOT / "bin" / "javalint.jar")
    ]
    # reporter path stays repo-relative (javalint reads it cwd-relative, unlike
    # erclint's absolute paths); the java sink is passed through.
    assert "--reporters=Rep.java#Rep.report" in argv
    assert argv[-1] == "a.java"


def _assert_erclint_splits_usage_flag(spec):
    argv = spec.build_argv(
        Path("/repo"),
        Path("/tb"),
        ["pkg"],
        (("rep.go", "myReport", "capture"), ("cli.go", "usage", "usage")),
    )
    assert f"--reporters={Path('/repo') / 'rep.go'}#myReport" in argv
    assert f"--usage-sinks={Path('/repo') / 'cli.go'}#usage" in argv


def test_dev_erclint_argv_splits_capture_and_usage_flags(monkeypatch):
    monkeypatch.setattr(
        engines, "_built_go_binary", lambda root, name: Path("/tb/bin") / name
    )
    _assert_erclint_splits_usage_flag(next(e for e in DEV_ENGINES if e.id == "erclint"))


def test_hermetic_erclint_argv_splits_capture_and_usage_flags():
    _assert_erclint_splits_usage_flag(
        next(e for e in engines.HERMETIC_ENGINES if e.id == "erclint")
    )


def test_eslint_argv_drops_usage_declarations():
    es = next(e for e in DEV_ENGINES if e.id == "tackbox-eslint")
    argv = es.build_argv(
        Path("/repo"),
        Path("/tb"),
        ["a.js"],
        (("rep.js", "myReport", "capture"), ("cli.js", "usage", "usage")),
    )
    assert "--reporters=rep.js#myReport" in argv
    assert not any("cli.js" in a for a in argv)


def test_dev_engines_eslint_covers_ts_and_svelte():
    es = next(e for e in DEV_ENGINES if e.id == "tackbox-eslint")
    assert {".js", ".ts", ".svelte"} <= es.extensions


def test_dev_engines_mdlint_extension_is_only_md():
    md = next(e for e in DEV_ENGINES if e.id == "tackbox-mdlint")
    assert md.extensions == frozenset([".md"])


# -- resolve_dev_versions ---------------------------------------------------


def test_resolve_versions_erclint_degrades_to_question_without_go(
    monkeypatch, tmp_path
):
    # No Go toolchain: the dev-binary build fails and the banner must show
    # "?" for erclint instead of crashing the whole CLI.
    def _no_go(_root, _name):
        raise FileNotFoundError("go")

    monkeypatch.setattr(engines, "_built_go_binary", _no_go)
    versions = engines.resolve_dev_versions(tmp_path)
    assert versions["erclint"] == "?"

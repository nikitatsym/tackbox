"""Unit tests for engine dispatch and helpers.

End-to-end wiring (subprocess run against real binaries + fixture repo)
lives in test_cli_fixture.py; here the tests are pure logic on the
public surface of tackbox.engines.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
    from tackbox.engines import DEV_ENGINES

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
    from tackbox.engines import DEV_ENGINES

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


# -- DEV_ENGINES registry (shape checks, not behavior) --------------------


def test_dev_engines_registry_order_locked():
    assert [e.id for e in DEV_ENGINES] == [
        "erclint",
        "erclint-opengrep",
        "tackbox-eslint",
        "tackbox-mdlint",
    ]


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
    # svelte parser not available in opengrep - excluded.
    assert ".svelte" not in og.extensions


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
    import tackbox.engines as engines

    def _no_go(_root, _name):
        raise FileNotFoundError("go")

    monkeypatch.setattr(engines, "_built_go_binary", _no_go)
    versions = engines.resolve_dev_versions(tmp_path)
    assert versions["erclint"] == "?"

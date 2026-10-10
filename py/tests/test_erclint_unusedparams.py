"""ERC011 selection and engine failures at the raw result boundary."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import init_repo, tackbox_env
from tackbox import cli, engines
from tackbox.engines import EngineResult


PARAM = {"posn": "/repo/p.go:2:13", "message": "ERC011: unused parameter x", "variant": "plain"}
PRESENCE = {"record": "erc011-package-variant-v1", "variant": "internal-test"}


def normalize(*docs):
    return engines.normalize_erclint_result(EngineResult("erclint", 0, "".join(json.dumps(doc) for doc in docs), ""))


def test_zero_findings_internal_variant_normalized_before_cache_and_renderers(tmp_path):
    result = normalize({"p": {"unusedparams": [PARAM], "unused": [{"posn": "/repo/p.go:1:1", "message": "ERC010: dead"}]}},
                       {"p": {"unusedparams": [PRESENCE]}})
    assert "ERC011" not in result.stdout and "record" not in result.stdout
    assert "ERC010" in result.stdout
    assert cli._aggregate_exit([result]) == 1
    assert [f.rule for f in cli._located([result], Path("/repo"))] == ["unused"]
    filtered = cli._filter_erclint_result(result, Path("/repo"), frozenset({"p.go"}))
    assert not filtered.stdout
    clean = normalize({"p": {"unusedparams": [PARAM]}}, {"p": {"unusedparams": [PRESENCE]}})
    info = {"arg_digest": [(".", "digest")], "arg_ip": {".": "p"}}
    assert cli._clean_args(clean, info) == ["."]
    cli._mark_clean_units([clean], {"erclint": info}, "hash", tmp_path)
    assert list(tmp_path.rglob("*.erclint"))
    assert cli._aggregate_exit([clean]) == 0
    assert cli._located([clean], Path("/repo")) == []


@pytest.mark.parametrize("reverse", [False, True])
def test_external_variant_never_suppresses_production(reverse):
    external = {**PARAM, "variant": "external-test", "posn": "/repo/p_test.go:3:1"}
    docs = [{"p": {"unusedparams": [PARAM]}, "q": {"unusedparams": [PARAM]}},
            {"p_test [p.test]": {"unusedparams": [PRESENCE, external]}}]
    result = normalize(*(reversed(docs) if reverse else docs))
    assert {f["pkg"] for f in engines.parse_erclint_findings(result.stdout)} == {"p", "q", "p_test [p.test]"}
    scoped = normalize({"p": {"unusedparams": [PRESENCE]}}, *docs)
    assert {f["pkg"] for f in engines.parse_erclint_findings(scoped.stdout)} == {"q", "p_test [p.test]"}


def test_internal_findings_survive_and_plain_is_widest_without_presence():
    test = {**PARAM, "variant": "internal-test", "posn": "/repo/p_test.go:3:1"}
    result = normalize({"p": {"unusedparams": [PARAM]}}, {"p": {"unusedparams": [PRESENCE, test]}})
    parsed = json.loads(result.stdout)
    assert parsed == {"p": {"unusedparams": [{key: value for key, value in test.items() if key != "variant"}]}}
    plain = normalize({"p": {"unusedparams": [PARAM]}})
    assert cli._aggregate_exit([plain]) == 1


@pytest.mark.parametrize("raw", ['[]', '{"p": []}', '{"p": {"unusedparams": {}}}',
                                  '{"p": {"unusedparams": [null]}}',
                                  '{"p": {"unusedparams": [{"record": "unknown"}]}}',
                                  '{"p": {"unusedparams": [{"variant": []}]}}',
                                  '{"p": {"unusedparams": [{"variant": "plain"}]}}',
                                  '{"p": {"unusedparams": [{"variant": "wrong"}]}}', '{'])
def test_malformed_payload_fails_loudly_without_renderer_crash(raw):
    result = engines.normalize_erclint_result(EngineResult("erclint", 0, raw, ""))
    assert result.exit_code == 2 and "invalid erclint output" in result.stderr
    assert not result.stdout
    assert cli._located([result], Path("/repo")) == []


def test_analyzer_failure_preserved_but_not_a_finding():
    result = normalize({"p": {"unusedparams": {"error": engines.COMPILE_SKIP},
                               "unused": {"error": engines.COMPILE_SKIP}}},
                       {"p": {"errcheck": {"error": "another\nerror"}}},
                       {"p [p.test]": {"unusedparams": {"error": engines.COMPILE_SKIP}}},
                       {"p_test [p.test]": {"unused": {"error": engines.COMPILE_SKIP}}},
                       {"q": {"unused": {"error": engines.COMPILE_SKIP}}})
    assert result.exit_code == 1 and "failed for 'p'" in result.stderr
    assert engines.COMPILE_SKIP in result.stdout
    assert engines.erclint_compile_broken_pkgs(result.stdout) == ["p", "q"]
    assert cli._located([result], Path("/repo")) == []
    assert len(result.stderr.splitlines()) == 2
    assert "unused, unusedparams" in result.stderr and "another error" in result.stderr
    assert len(list(engines.iter_json_objects(result.stdout))) == 1


def test_error_and_other_findings_survive_variant_selection():
    result = normalize({"p": {"unusedparams": [PARAM, PRESENCE],
                               "errcheck": {"error": "analyzer failed"},
                               "unused": [{"posn": "/repo/p.go:1:1", "message": "ERC010: dead"}]}})
    assert result.exit_code == 1 and "analyzer failed" in result.stderr
    assert "analyzer failed" in result.stdout and "ERC010" in result.stdout
    assert "ERC011" not in result.stdout and "record" not in result.stdout
    assert [f.rule for f in cli._located([result], Path("/repo"))] == ["unused"]


@pytest.mark.parametrize("codequality", [False, True])
def test_widest_variant_clean_in_console_report_and_warm_cache(tmp_path, codequality):
    (tmp_path / "go.mod").write_text("module widestfixture\n\ngo 1.27.0\n")
    (tmp_path / "p.go").write_text("package p\nfunc helper(x int) int { return 1 }\nfunc Public() { helper(1) }\n")
    tests = tmp_path / "p_test.go"
    tests.write_text('package p\nimport "testing"\nvar forced func(int) int = helper\n'
                     'func TestUse(t *testing.T) { forced(1) }\n')
    init_repo(tmp_path, commit=True)
    args = [sys.executable, "-m", "tackbox.cli", "lint", "."]
    report = tmp_path / "quality.json"
    if codequality:
        args += ["--codequality", str(report)]
    cache = tmp_path / "cache"
    env = tackbox_env(TACKBOX_CACHE_HOME=str(cache))
    cold = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert cold.returncode == 0, cold.stdout + cold.stderr
    assert "== erclint ==" in cold.stdout and "ERC011" not in cold.stdout
    assert list(cache.rglob("*.erclint"))
    if codequality:
        assert json.loads(report.read_text()) == []
    warm = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert warm.returncode == 0 and "== erclint ==" not in warm.stdout
    tests.write_text('package p\nimport "testing"\nfunc TestUse(t *testing.T) { helper(1) }\n')
    invalidated = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert invalidated.returncode == 1 and "ERC011" in invalidated.stdout
    if codequality:
        assert any(item["check_name"] == "unusedparams" for item in json.loads(report.read_text()))


def test_runner_normalizes_before_return(monkeypatch, tmp_path):
    (tmp_path / "go.mod").write_text("module p\n")
    spec = next(e for e in engines.DEV_ENGINES if e.id == "erclint")
    raw = json.dumps({"p": {"unusedparams": [PARAM, PRESENCE]}}).encode()
    monkeypatch.setattr(engines, "_machine_argv", lambda *args: [])
    monkeypatch.setattr(engines, "_built_go_binary", lambda *args: "/erclint")
    monkeypatch.setattr(engines.proc, "run_bytes", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, raw, b""))
    result = engines._run_per_module(engines.EngineRun(spec, ["."], tmp_path, tmp_path, (), False))
    assert result.stdout == "" and result.exit_code == 0


@pytest.mark.parametrize("codequality", [False, True])
def test_typecheck_failure_no_traceback_and_writes_codequality(tmp_path, codequality):
    (tmp_path / "go.mod").write_text("module brokenfixture\n\ngo 1.27.0\n")
    (tmp_path / "broken.go").write_text("package broken\nfunc Public() { missing() }\n")
    init_repo(tmp_path, commit=True)
    args = [sys.executable, "-m", "tackbox.cli", "lint", ".", "--no-cache"]
    report = tmp_path / "quality.json"
    if codequality:
        args += ["--codequality", str(report)]
    result = subprocess.run(args, cwd=tmp_path, env=tackbox_env(), capture_output=True, text=True)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "erclint analyzer" in result.stderr and "failed" in result.stderr
    if codequality:
        assert json.loads(report.read_text()) == []


@pytest.mark.parametrize("no_cache", [False, True])
def test_only_build_excluded_directory_skipped_but_normal_package_analyzed(tmp_path, no_cache):
    (tmp_path / "go.mod").write_text("module selectedfixture\n\ngo 1.27.0\n")
    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "fixture.go").write_text("//go:build ignore\n\npackage ignored\nfunc Broken() { missing() }\n")
    normal = tmp_path / "normal"
    normal.mkdir()
    (normal / "p.go").write_text("package p\nfunc helper(x int) int { return 1 }\nfunc Public() { helper(1) }\n")
    init_repo(tmp_path, commit=True)
    args = [sys.executable, "-m", "tackbox.cli", "lint", "."]
    if no_cache:
        args += ["--no-cache"]
    result = subprocess.run(args, cwd=tmp_path, env=tackbox_env(), capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ERC011" in result.stdout
    assert "build constraints exclude" not in result.stderr and "Traceback" not in result.stderr
    (normal / "p.go").write_text("package p\nfunc Public() { missing() }\n")
    broken = subprocess.run(args, cwd=tmp_path, env=tackbox_env(), capture_output=True, text=True)
    assert broken.returncode != 0 and "missing" in broken.stderr
    assert "Traceback" not in broken.stderr

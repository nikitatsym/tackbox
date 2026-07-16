"""Fixtures for the root dev.py orchestrator (dev-script contract).

dev.py is a thin dispatcher over lint / test / e2e / check; these pin the
contract (e2e no-op, check = lint + test with both always run and failures
surfaced, usage exit code) without re-running the whole suite. The real
end-to-end proof is `python dev.py check` in acceptance.
"""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dev():
    spec = importlib.util.spec_from_file_location("devscript", ROOT / "dev.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dev = _load_dev()


def test_e2e_is_noop():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = dev.e2e()
    assert rc == 0
    assert "no e2e tests found" in buf.getvalue()


def test_check_runs_lint_then_test(monkeypatch):
    calls = []
    monkeypatch.setattr(dev, "lint", lambda: calls.append("lint") or 0)
    monkeypatch.setattr(dev, "test", lambda: calls.append("test") or 0)
    assert dev.check() == 0
    assert calls == ["lint", "test"]


def test_check_surfaces_lint_failure(monkeypatch):
    monkeypatch.setattr(dev, "lint", lambda: 1)
    monkeypatch.setattr(dev, "test", lambda: 0)
    assert dev.check() != 0


def test_check_runs_test_even_when_lint_fails(monkeypatch):
    # check aggregates: a lint failure must not skip the test suite, and the
    # aggregate exit is non-zero. Attacks the "check swallows a red lint" bug.
    calls = []
    monkeypatch.setattr(dev, "lint", lambda: calls.append("lint") or 1)
    monkeypatch.setattr(dev, "test", lambda: calls.append("test") or 0)
    assert dev.check() != 0
    assert calls == ["lint", "test"]


def test_unknown_command_exits_2():
    r = subprocess.run(
        [sys.executable, str(ROOT / "dev.py"), "bogus"], capture_output=True, text=True
    )
    assert r.returncode == 2
    assert "usage" in r.stderr


def test_no_command_exits_2():
    r = subprocess.run(
        [sys.executable, str(ROOT / "dev.py")], capture_output=True, text=True
    )
    assert r.returncode == 2


# --- artifact-based test discovery (closes the silent-coverage-hole) ----------
#
# dev.py used to hand-list runners, so py/tackbox_report (34 tests) and
# java/report were never run by `check`. These pin the auto-discovery contract:
# every pyproject-with-tests and every root pom is found, with no allow/deny list.

_POM_NS = '<?xml version="1.0" encoding="UTF-8"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0">\n'


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _pom(artifact: str, modules: list[str] | None = None) -> str:
    mods = ""
    if modules:
        inner = "".join(f"    <module>{m}</module>\n" for m in modules)
        mods = f"  <modules>\n{inner}  </modules>\n"
    return (
        f"{_POM_NS}  <modelVersion>4.0.0</modelVersion>\n"
        f"  <groupId>x</groupId><artifactId>{artifact}</artifactId><version>1</version>\n"
        f"{mods}</project>\n"
    )


def _rel(paths, root):
    return {str(Path(p).relative_to(root)) for p in paths}


def test_pyproject_with_tests_yields_a_runner(tmp_path):
    _write(tmp_path / "proj" / "pyproject.toml")
    (tmp_path / "proj" / "tests").mkdir(parents=True)
    assert dev._python_test_dirs(tmp_path) == [tmp_path / "proj"]
    (cmd,) = dev._python_runners(tmp_path)
    assert cmd == ["uv", "run", "--directory", str(tmp_path / "proj"),
                   "--group", "dev", "pytest", "-q"]


def test_pyproject_without_tests_is_skipped_and_announced(tmp_path):
    _write(tmp_path / "lib" / "pyproject.toml")  # no tests/ dir
    buf = io.StringIO()
    with redirect_stdout(buf):
        dirs = dev._python_test_dirs(tmp_path)
    assert dirs == []  # skipped...
    assert "dev.py: no tests/ in lib, skipped" in buf.getvalue()  # ...but never silently


def test_maven_module_child_is_not_run_standalone(tmp_path):
    _write(tmp_path / "pom.xml", _pom("parent", modules=["child"]))
    _write(tmp_path / "child" / "pom.xml", _pom("child"))
    assert _rel(dev._maven_root_poms(tmp_path), tmp_path) == {"pom.xml"}


def test_poms_under_pruned_dirs_are_ignored(tmp_path):
    _write(tmp_path / "target" / "pom.xml", _pom("in-target"))
    _write(tmp_path / "node_modules" / "dep" / "pom.xml", _pom("in-nm"))
    _write(tmp_path / "build" / "pkg" / "pyproject.toml")
    (tmp_path / "build" / "pkg" / "tests").mkdir(parents=True)
    assert dev._maven_root_poms(tmp_path) == []
    assert dev._python_test_dirs(tmp_path) == []


def test_repo_shaped_tree_discovers_both_python_and_both_maven_suites(tmp_path):
    # ADVERSARIAL / regression for the incident: a tree shaped like the real repo
    # must surface BOTH python suites (py + the nested py/tackbox_report) and BOTH
    # standalone poms (java + java/report). The old hand-list ran only one of each.
    for d in ("py", "py/tackbox_report"):
        _write(tmp_path / d / "pyproject.toml")
        (tmp_path / d / "tests").mkdir(parents=True)
    _write(tmp_path / "java" / "pom.xml", _pom("javalint"))            # no <modules>
    _write(tmp_path / "java" / "report" / "pom.xml", _pom("report"))  # standalone
    assert _rel(dev._python_test_dirs(tmp_path), tmp_path) == {"py", "py/tackbox_report"}
    assert _rel(dev._maven_root_poms(tmp_path), tmp_path) == {
        "java/pom.xml", "java/report/pom.xml"
    }


def test_real_repo_discovery_contract():
    # Pins the live contract: adding a suite later is picked up with no dev.py
    # edit. If this changes, discovery (not a hard-coded list) changed.
    root = dev._ROOT
    assert _rel(dev._python_test_dirs(root), root) == {"py", "py/tackbox_report"}
    assert _rel(dev._maven_root_poms(root), root) == {"java/pom.xml", "java/report/pom.xml"}


def test_test_runs_every_discovered_runner_even_after_a_failure(monkeypatch):
    # test() must invoke go, npm, and every discovered python + maven runner, and
    # one failing runner must not short-circuit the rest (all-runners-run).
    monkeypatch.setattr(dev, "_python_runners", lambda *a, **k: [["PY1"], ["PY2"]])
    monkeypatch.setattr(dev, "_maven_runners", lambda *a, **k: [["MV1"], ["MV2"]])
    ran = []

    def fake_run(cmd):
        ran.append(cmd)
        return 1 if cmd == ["PY1"] else 0  # a mid-list failure

    monkeypatch.setattr(dev, "_run", fake_run)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = dev.test()
    assert ran == [
        ["go", "test", "-race", "-count=1", "./go/..."], ["npm", "test"],
        ["PY1"], ["PY2"], ["MV1"], ["MV2"],
    ]
    assert rc != 0  # the PY1 failure surfaces
    assert "dev.py: npm test" in buf.getvalue()  # each runner announced before it runs

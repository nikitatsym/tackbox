"""End-to-end `.tackbox-reporters` behavior through the real CLI.

Covers the transport the unit / analysistest layers bypass: the CLI parsing
the file, threading declarations into each engine, engine-side symbol
validation (scope-independent), and the BrokenPipe guard.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

TACKBOX_ROOT = Path(__file__).resolve().parents[2]

GO_MOD = "module fixture\n\ngo 1.21\n"

GO_DECLARED = """package fixture

import "errors"

func myReport(err error) {}

func Handler() error {
\terr := errors.New("x")
\tif err != nil {
\t\tmyReport(err)
\t}
\treturn errors.New("noop")
}
"""

JS_DECLARED = """export function myReport(m, e) {}

try {
  f()
} catch (e) {
  myReport('handled it', e)
}
"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(TACKBOX_ROOT / "py")
    return env


def _lint(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "lint", *(extra or (".",)), "--no-cache"],
        cwd=root,
        env=_env(),
        capture_output=True,
        text=True,
    )


def test_go_declaration_recognized(tmp_path):
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "rep.go").write_text(GO_DECLARED)
    (tmp_path / ".tackbox-reporters").write_text("rep.go#myReport: local go sink\n")
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 0, f"declared go sink should make the err-branch clean:\n{r.stdout}\n{r.stderr}"


def test_go_dead_symbol_exit_2(tmp_path):
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "rep.go").write_text(GO_DECLARED)
    (tmp_path / ".tackbox-reporters").write_text("rep.go#nope: dead\n")
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 2, f"dead go symbol must exit 2:\n{r.stdout}\n{r.stderr}"
    assert "no top-level function nope" in r.stderr, r.stderr


def test_js_declaration_recognized(tmp_path):
    (tmp_path / "app.js").write_text(JS_DECLARED)
    (tmp_path / ".tackbox-reporters").write_text("app.js#myReport: local js sink\n")
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 0, f"declared js sink should satisfy no-swallow-catch:\n{r.stdout}\n{r.stderr}"


def test_js_dead_symbol_exit_2(tmp_path):
    (tmp_path / "app.js").write_text(JS_DECLARED)
    (tmp_path / ".tackbox-reporters").write_text("app.js#nope: dead\n")
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 2, f"dead js symbol must exit 2:\n{r.stdout}\n{r.stderr}"
    assert "no top-level function nope" in r.stderr, r.stderr


def test_js_dead_symbol_scope_independent(tmp_path):
    # `sink.js` is declared but NOT in the lint scope (`app.js`). eslint still
    # runs (app.js is JS), so it must validate every js declaration.
    (tmp_path / "app.js").write_text("console.log('ok')\n")
    (tmp_path / "sink.js").write_text("export function realReport(m, e) {}\n")
    (tmp_path / ".tackbox-reporters").write_text("sink.js#nope: dead\n")
    _init(tmp_path)
    r = _lint(tmp_path, "app.js")
    assert r.returncode == 2, f"scoped run must still validate out-of-scope declarations:\n{r.stdout}\n{r.stderr}"
    assert "no top-level function nope" in r.stderr, r.stderr


def test_broken_pipe_exit_141(tmp_path):
    # `head -c 0` closes the read end before tackbox writes; the guarded flush
    # then hits a closed pipe. The guard must exit 141 with no traceback rather
    # than crash on BrokenPipeError.
    blocks = "\n".join(
        f"def f{i}():\n    try:\n        g()\n    except ValueError:\n        pass\n"
        for i in range(20)
    )
    (tmp_path / "big.py").write_text(blocks)
    _init(tmp_path)
    cmd = (
        f"{sys.executable} -m tackbox.cli lint . --no-cache | head -c 0 >/dev/null; "
        "echo EXIT=${PIPESTATUS[0]}"
    )
    r = subprocess.run(
        ["bash", "-c", cmd], cwd=tmp_path, env=_env(), capture_output=True, text=True
    )
    assert "EXIT=141" in r.stdout, f"expected tackbox exit 141 on broken pipe:\n{r.stdout}\n{r.stderr}"
    assert "Traceback" not in r.stderr, r.stderr
    assert "BrokenPipeError" not in r.stderr, r.stderr

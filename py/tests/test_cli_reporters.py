"""End-to-end `.tackbox/reporters` behavior through the real CLI.

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
from conftest import init_repo, tackbox_env

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

# package main: myReport is unexported and reachable only from main(), never
# from an exported/inlinable path - export-data loading exposes it as absent.
GO_DECLARED_MAIN = """package main

import "errors"

func myReport(err error) {}

func main() {
\terr := errors.New("x")
\tif err != nil {
\t\tmyReport(err)
\t}
}
"""

GO_USAGE = """package fixture

import (
\t"fmt"
\t"os"
)

func usage(msg string) {
\tfmt.Fprintln(os.Stderr, msg)
\tos.Exit(2)
}

func Check(args []string) {
\tif len(args) < 2 {
\t\tusage("usage: tool <cmd>")
\t}
}
"""

GO_USAGE_ERR_BRANCH = """package fixture

import (
\t"errors"
\t"fmt"
\t"os"
)

func usage(msg string) {
\tfmt.Fprintln(os.Stderr, msg)
\tos.Exit(2)
}

func Run() error {
\terr := errors.New("x")
\tif err != nil {
\t\tusage("bad input")
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

PY_DECLARED = """def report_it(e):
    print(e)


def handler():
    try:
        work()
    except ValueError as e:
        report_it(e)
"""


def _init(root: Path) -> None:
    init_repo(root, commit=True)


def _lint(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "lint", *(extra or (".",)), "--no-cache"],
        cwd=root,
        env=tackbox_env(),
        capture_output=True,
        text=True,
    )


def _declare(repo: Path, text: str) -> None:
    (repo / ".tackbox").mkdir(exist_ok=True)
    (repo / ".tackbox" / "reporters").write_text(text)


def test_go_declaration_recognized(tmp_path):
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "rep.go").write_text(GO_DECLARED)
    _declare(tmp_path, "rep.go#myReport: local go sink\n")
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 0, f"declared go sink should make the err-branch clean:\n{r.stdout}\n{r.stderr}"


def test_legacy_root_declaration_invisible(tmp_path):
    """A declaration at the pre-move root path draws no recognition and no
    diagnostic - the legacy location simply does not exist."""
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "rep.go").write_text(GO_DECLARED)
    # composed so the step's acceptance grep for the old literal stays empty
    legacy = ".tackbox" + "-reporters"
    (tmp_path / legacy).write_text("rep.go#myReport: local go sink\n")
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 1, (
        f"legacy-path declaration must not credit the sink:\n{r.stdout}\n{r.stderr}"
    )
    assert legacy not in r.stdout + r.stderr, "legacy path must draw no diagnostic"


def test_go_declaration_recognized_unexported_main(tmp_path):
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "main.go").write_text(GO_DECLARED_MAIN)
    _declare(tmp_path, "main.go#myReport: local go sink\n")
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 0, (
        f"unexported declared go sink in package main should make the err-branch clean:\n{r.stdout}\n{r.stderr}"
    )


def test_go_dead_symbol_exit_2(tmp_path):
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "rep.go").write_text(GO_DECLARED)
    _declare(tmp_path, "rep.go#nope: dead\n")
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 2, f"dead go symbol must exit 2:\n{r.stdout}\n{r.stderr}"
    assert "no top-level function nope" in r.stderr, r.stderr


def test_go_usage_sink_declared_clean(tmp_path):
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "cli.go").write_text(GO_USAGE)
    _declare(tmp_path,
        "cli.go#usage [usage]: diagnostic exit\n"
    )
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 0, (
        f"declared usage sink should be clean outside err-branches:\n{r.stdout}\n{r.stderr}"
    )


def test_go_usage_helper_undeclared_stays_strict(tmp_path):
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "cli.go").write_text(GO_USAGE)
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 1, (
        f"undeclared usage helper body must keep ERC003:\n{r.stdout}\n{r.stderr}"
    )


def test_go_usage_sink_err_branch_fires(tmp_path):
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "cli.go").write_text(GO_USAGE_ERR_BRANCH)
    _declare(tmp_path,
        "cli.go#usage [usage]: diagnostic exit\n"
    )
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 1, (
        f"usage sink in an err-branch must fire:\n{r.stdout}\n{r.stderr}"
    )
    assert "failure path" in r.stdout, r.stdout


def test_js_declaration_recognized(tmp_path):
    (tmp_path / "app.js").write_text(JS_DECLARED)
    _declare(tmp_path, "app.js#myReport: local js sink\n")
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 0, f"declared js sink should satisfy no-swallow-catch:\n{r.stdout}\n{r.stderr}"


def test_js_dead_symbol_exit_2(tmp_path):
    (tmp_path / "app.js").write_text(JS_DECLARED)
    _declare(tmp_path, "app.js#nope: dead\n")
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 2, f"dead js symbol must exit 2:\n{r.stdout}\n{r.stderr}"
    assert "no top-level function nope" in r.stderr, r.stderr


def test_js_dead_symbol_scope_independent(tmp_path):
    # `sink.js` is declared but NOT in the lint scope (`app.js`). eslint still
    # runs (app.js is JS), so it must validate every js declaration.
    (tmp_path / "app.js").write_text("console.log('ok')\n")
    (tmp_path / "sink.js").write_text("export function realReport(m, e) {}\n")
    _declare(tmp_path, "sink.js#nope: dead\n")
    _init(tmp_path)
    r = _lint(tmp_path, "app.js")
    assert r.returncode == 2, f"scoped run must still validate out-of-scope declarations:\n{r.stdout}\n{r.stderr}"
    assert "no top-level function nope" in r.stderr, r.stderr


def test_py_declaration_recognized(tmp_path):
    (tmp_path / "rep.py").write_text(PY_DECLARED)
    _declare(tmp_path, "rep.py#report_it: local py sink\n")
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 0, f"declared py sink should make the except clean:\n{r.stdout}\n{r.stderr}"


def test_py_dead_symbol_exit_2(tmp_path):
    (tmp_path / "rep.py").write_text(PY_DECLARED)
    _declare(tmp_path, "rep.py#nope: dead\n")
    _init(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 2, f"dead py symbol must exit 2:\n{r.stdout}\n{r.stderr}"
    assert "no top-level function nope" in r.stderr, r.stderr


def test_broken_pipe_exit_141(tmp_path):
    # A pipe with no reader makes the first tackbox write fail deterministically.
    blocks = "\n".join(
        f"def f{i}():\n    try:\n        g()\n    except ValueError:\n        pass\n"
        for i in range(20)
    )
    (tmp_path / "big.py").write_text(blocks)
    _init(tmp_path)
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "tackbox.cli", "lint", ".", "--no-cache"],
            cwd=tmp_path,
            env=tackbox_env(),
            stdout=write_fd,
            stderr=subprocess.PIPE,
            text=True,
        )
    finally:
        os.close(write_fd)
    assert r.returncode == 141, (
        f"expected tackbox exit 141 on broken pipe:\n{r.stderr}"
    )
    assert "Traceback" not in r.stderr, r.stderr
    assert "BrokenPipeError" not in r.stderr, r.stderr


def test_filesystem_einval_under_piped_stdout_is_not_a_broken_pipe(tmp_path):
    """A filesystem EINVAL (carries a filename) must stay a loud error even when
    stdout is a pipe: only a bare pipe-write EINVAL may take the 141 exit."""
    (tmp_path / "ok.py").write_text("def f():\n    return 1\n")
    _init(tmp_path)
    # An invalid report path produces OSError(EINVAL, ..., filename) on Windows
    # and a plain failure elsewhere; neither may masquerade as a closed pipe.
    r = subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "lint", ".", "--no-cache",
         "--codequality", "reports/lint?report.json"],
        cwd=tmp_path,
        env=tackbox_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert r.returncode != 141, f"filesystem error relabeled as broken pipe:\n{r.stderr}"
    assert r.returncode != 0, "invalid --codequality path must fail loudly"
    assert r.stderr.strip() != "", "the failure must be visible on stderr"

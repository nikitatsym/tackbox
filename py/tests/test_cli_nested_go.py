"""Step A acceptance: repos whose go.mod is not at the repo root.

Real consumers keep the Go module in a subdir (thrift-nats: `go/go.mod`);
tackbox 0.1.14 crashed on them with a raw CalledProcessError out of
`go list`. These fixtures pin the fixed behavior: per-module erclint
runs, module-scoped digests, orphan `.go` warning, and a clean exit 2
on a broken go.mod. Fixture files are generated inline so tackbox
self-lint never encounters them.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


GO_MOD_NESTED = """module nestedfixture

go 1.24
"""

GO_MOD_ALPHA = """module alphafixture

go 1.24
"""

GO_MOD_BETA = """module betafixture

go 1.24
"""

GO_SWALLOW = """package pkg

import "errors"

func Fail() error {
\terr := errors.New("boom")
\tif err != nil {
\t\t_ = "swallowed"
\t}
\treturn errors.New("noop")
}
"""

GO_CLEAN_MAIN = """package main

func main() {}
"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")


def _run_tackbox(repo: Path, *argv: str) -> subprocess.CompletedProcess:
    tackbox_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tackbox_root / "py")
    return subprocess.run(
        [sys.executable, "-m", "tackbox.cli", *argv],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def _erclint_section(stdout: str) -> str:
    m = re.search(r"^== erclint ==\n(?P<body>.*?)(?=^== |\Z)", stdout, re.M | re.S)
    return m.group("body") if m else ""


def _nested_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "go" / "pkg").mkdir(parents=True)
    (root / "go" / "go.mod").write_text(GO_MOD_NESTED)
    (root / "go" / "pkg" / "swallow.go").write_text(GO_SWALLOW)
    _init_repo(root)
    return root


def test_nested_module_lints_and_reports(tmp_path):
    repo = _nested_repo(tmp_path)
    result = _run_tackbox(repo, "lint", ".", "--no-cache")
    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode == 1, (
        f"expected 1, got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    section = _erclint_section(result.stdout)
    assert "ERC001" in section
    assert "swallow.go" in section


def test_nested_module_with_cache_enabled_digests_do_not_crash(tmp_path):
    """The 0.1.14 crash came from the digest layer (`go list` at repo root)."""
    repo = _nested_repo(tmp_path)
    result = _run_tackbox(repo, "lint", ".")
    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode == 1
    assert "ERC001" in _erclint_section(result.stdout)


def test_two_modules_both_linted(tmp_path):
    root = tmp_path / "repo"
    (root / "alpha" / "pkg").mkdir(parents=True)
    (root / "alpha" / "go.mod").write_text(GO_MOD_ALPHA)
    (root / "alpha" / "pkg" / "swallow.go").write_text(GO_SWALLOW)
    (root / "beta" / "lib").mkdir(parents=True)
    (root / "beta" / "go.mod").write_text(GO_MOD_BETA)
    (root / "beta" / "lib" / "swallow.go").write_text(GO_SWALLOW)
    _init_repo(root)

    result = _run_tackbox(root, "lint", ".", "--no-cache")
    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode == 1
    section = _erclint_section(result.stdout)
    assert "alpha" in section and "beta" in section, section


def test_orphan_go_file_warns_and_continues(tmp_path):
    root = tmp_path / "repo"
    (root / "go" / "pkg").mkdir(parents=True)
    (root / "go" / "go.mod").write_text(GO_MOD_NESTED)
    (root / "go" / "pkg" / "swallow.go").write_text(GO_SWALLOW)
    (root / "scripts").mkdir()
    (root / "scripts" / "tool.go").write_text(GO_CLEAN_MAIN)
    _init_repo(root)

    result = _run_tackbox(root, "lint", ".", "--no-cache")
    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode == 1
    assert "no enclosing go.mod" in result.stderr
    assert "scripts" in result.stderr
    section = _erclint_section(result.stdout)
    assert "swallow.go" in section
    assert "tool.go" not in section


def test_broken_go_mod_fails_with_clean_message(tmp_path):
    root = tmp_path / "repo"
    (root / "go" / "pkg").mkdir(parents=True)
    (root / "go" / "go.mod").write_text("garbage directive\n")
    (root / "go" / "pkg" / "ok.go").write_text(GO_CLEAN_MAIN)
    _init_repo(root)

    result = _run_tackbox(root, "lint", ".")
    assert result.returncode == 2, (
        f"expected 2, got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert "go.mod" in result.stderr or "go list" in result.stderr

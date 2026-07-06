"""Materialize-fixture spec: inline constants -> tmp git repo, deterministic.

Fixtures are the spec. Any drift in seeded violations (planted ERC001,
no-swallow-catch, MD non-ASCII) must trip content assertions here rather
than surface only as a wheels-e2e failure on some CI runner.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "materialize_fixture.py"


def _tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/"):
            continue
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _run(target: Path) -> None:
    subprocess.run(
        [sys.executable, str(SCRIPT), str(target)],
        check=True, capture_output=True, text=True,
    )


def test_materialize_creates_expected_layout(tmp_path):
    root = tmp_path / "fx"
    _run(root)
    assert (root / "go.mod").is_file()
    assert (root / "pkga" / "violate.go").is_file()
    assert (root / "pkgb" / "secret.go").is_file()
    assert (root / "swallow.js").is_file()
    assert (root / "notes.md").is_file()
    assert (root / "Handler.java").is_file()
    assert (root / "javasub" / "Deep.java").is_file()
    assert (root / ".git").is_dir()


def test_planted_violations_are_present(tmp_path):
    root = tmp_path / "fx"
    _run(root)
    erc001 = (root / "pkga" / "violate.go").read_text()
    assert "errors.New" in erc001 and "err != nil" in erc001
    erc006 = (root / "pkgb" / "secret.go").read_text()
    assert "sentryErr" in erc006 and "user.token" in erc006
    swallow = (root / "swallow.js").read_text()
    assert "catch" in swallow and swallow.count("{") == 2
    md = (root / "notes.md").read_text()
    assert "\u2014" in md, "em-dash (U+2014) is the mdlint violation carrier"
    handler = (root / "Handler.java").read_text()
    assert "catch (Exception e) {\n        }" in handler, "empty catch is the JV001 carrier"
    deep = (root / "javasub" / "Deep.java").read_text()
    assert "catch (Exception e) {\n        }" in deep, "nested empty catch is the JV001 carrier"


def test_materialize_is_deterministic(tmp_path):
    """Two runs must produce byte-identical content trees.

    Guards against inline constants drifting between test refactors, and
    against the git init step (which is not part of the digest) leaking
    non-deterministic bytes into tracked files.
    """
    a = tmp_path / "a"
    b = tmp_path / "b"
    _run(a)
    _run(b)
    assert _tree_digest(a) == _tree_digest(b)


def test_target_must_be_empty_or_absent(tmp_path):
    root = tmp_path / "fx"
    root.mkdir()
    (root / "prior.txt").write_text("existing content")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(root)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0, "must refuse to overwrite a non-empty dir"
    assert "not empty" in result.stderr.lower()


def test_git_repo_initialized_with_single_commit(tmp_path):
    root = tmp_path / "fx"
    _run(root)
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=root, check=True, capture_output=True, text=True,
    )
    assert len(log.stdout.strip().splitlines()) == 1
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root, check=True, capture_output=True, text=True,
    )
    assert status.stdout == "", "working tree must be clean after materialize"

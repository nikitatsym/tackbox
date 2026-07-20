from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from tackbox import doctor
from tackbox import engines as engines_mod
from tackbox.cache import sha256_tree


def _needs_git():
    if not shutil.which("git"):
        pytest.fail("`git` toolchain not found on PATH; install it, do not skip")


def test_dev_mode_summary_and_exit_zero():
    _needs_git()
    with mock.patch.object(engines_mod, "is_hermetic", return_value=False):
        out = io.StringIO()
        rc = doctor.run(out)
    text = out.getvalue()
    assert rc == 0
    lines = text.strip().splitlines()
    assert lines[-1].startswith("doctor: 8 checks, 0 failed")
    ids = {ln.split(" ", 2)[1].rstrip(":") for ln in lines[:-1]}
    assert ids == {
        "platform",
        "engines-store",
        "payload-checksums",
        "binaries-start",
        "git-in-path",
        "go-toolchain",
        "java-toolchain",
        "ast-grep",
    }
    assert all(ln.startswith("ok ") for ln in lines[:-1])


def test_dev_mode_skips_payload_and_binaries():
    _needs_git()
    with mock.patch.object(engines_mod, "is_hermetic", return_value=False):
        out = io.StringIO()
        doctor.run(out)
    text = out.getvalue()
    assert "ok engines-store: skipped (dev mode)" in text
    assert "ok payload-checksums: skipped (dev mode)" in text
    assert "ok binaries-start: skipped (dev mode)" in text


def _foreign_platform_key() -> str:
    """Any supported key guaranteed to differ from the host."""
    host = engines_mod.detect_platform_key()
    return "linux-x86_64" if host != "linux-x86_64" else "macos-aarch64"


def _setup_hermetic(tmp_path, monkeypatch, pkg, engines_json) -> None:
    """Point the hermetic engine store at a tmp pkg + empty payload dir so
    doctor runs offline against the supplied engines.json."""
    (pkg / "engines.json").write_text(json.dumps(engines_json))
    monkeypatch.setattr(engines_mod, "_TACKBOX_PKG_ROOT", pkg)
    monkeypatch.setattr(engines_mod, "is_hermetic", lambda: True)
    (tmp_path / "tackbox_engines" / "bin").mkdir(parents=True)
    monkeypatch.setenv("TACKBOX_ENGINES_DIR", str(tmp_path / "tackbox_engines"))
    monkeypatch.setattr(engines_mod, "hermetic_env", lambda base=None: dict(base or {}))


def test_hermetic_platform_mismatch_flags_check(tmp_path, monkeypatch):
    engines_json = {
        "schema": 1,
        "payload_sha256": "deadbeef",
        "platform": _foreign_platform_key(),
        "wheel_plat": "manylinux_2_28_x86_64",
        "engines": [],
    }
    pkg = tmp_path / "tackbox"
    pkg.mkdir()
    (pkg / "engines.json").write_text(json.dumps(engines_json))
    monkeypatch.setattr(engines_mod, "_TACKBOX_PKG_ROOT", pkg)
    monkeypatch.setattr(engines_mod, "is_hermetic", lambda: True)
    # Override the store with a supplied payload dir so ensure never fetches.
    (tmp_path / "tackbox_engines" / "bin").mkdir(parents=True)
    monkeypatch.setenv("TACKBOX_ENGINES_DIR", str(tmp_path / "tackbox_engines"))

    out = io.StringIO()
    rc = doctor.run(out)
    assert rc == 1
    text = out.getvalue()
    assert "fail platform:" in text
    assert f"wheel built for {_foreign_platform_key()}" in text
    assert "doctor: 8 checks, " in text


def test_hermetic_payload_mismatch_flags_check(tmp_path, monkeypatch):
    pkg = tmp_path / "tackbox"
    pkg.mkdir()
    (pkg / "bin").mkdir()
    bad = pkg / "bin" / "erclint"
    bad.write_bytes(b"not-the-real-binary")
    engines_json = {
        "schema": 1,
        "payload_sha256": "deadbeef",
        "engines": [
            {
                "id": "erclint",
                "kind": "binary",
                "version": "0.0.0",
                "path": "tackbox/bin/erclint",
                "sha256": "aa" * 32,
                "license": "MIT",
                "license_path": "",
            },
        ],
    }
    _setup_hermetic(tmp_path, monkeypatch, pkg, engines_json)

    out = io.StringIO()
    rc = doctor.run(out)
    assert rc == 1
    text = out.getvalue()
    assert "fail payload-checksums: mismatch=1" in text


def test_hermetic_missing_payload_flags_check(tmp_path, monkeypatch):
    pkg = tmp_path / "tackbox"
    pkg.mkdir()
    engines_json = {
        "schema": 1,
        "payload_sha256": "deadbeef",
        "engines": [
            {
                "id": "opengrep",
                "kind": "binary",
                "version": "1.25.0",
                "path": "tackbox_engines/bin/opengrep",
                "sha256": "aa" * 32,
                "license": "LGPL-2.1",
                "license_path": "",
            },
        ],
    }
    _setup_hermetic(tmp_path, monkeypatch, pkg, engines_json)

    out = io.StringIO()
    doctor.run(out)
    text = out.getvalue()
    assert "fail payload-checksums: missing=1" in text


def _commit_one_file(tmp_path: Path, name: str, content: str) -> None:
    """Commit a single file into a fresh repo at tmp_path - a source set of one,
    used to prove a toolchain is/isn't needed by what the tree actually holds."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / name).write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=tmp_path, check=True, capture_output=True,
    )


def test_go_toolchain_ok_when_source_set_has_no_go(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _commit_one_file(tmp_path, "hello.py", "print('hi')\n")

    with mock.patch("shutil.which", side_effect=lambda name: {"git": "/usr/bin/git", "go": None}.get(name)):
        result = doctor._check_go_toolchain()
    assert result.ok is True
    assert "not needed" in result.detail


# -- java-toolchain check --------------------------------------------------


def _java_repo(tmp_path: Path) -> None:
    """Commit one .java file so the source set needs the java toolchain."""
    _commit_one_file(tmp_path, "Handler.java", "class Handler {}\n")


def test_java_toolchain_ok_when_source_set_has_no_java(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _commit_one_file(tmp_path, "hello.py", "print('hi')\n")
    with mock.patch("shutil.which", side_effect=lambda n: {"git": "/usr/bin/git", "java": None}.get(n)):
        result = doctor._check_java_toolchain()
    assert result.ok is True
    assert "not needed" in result.detail


def test_java_toolchain_fails_when_needed_but_absent(tmp_path, monkeypatch):
    # Adversarial: source set has .java but `java` is off PATH -> loud fail,
    # never a silent pass that lets javalint be skipped.
    monkeypatch.chdir(tmp_path)
    _java_repo(tmp_path)
    with mock.patch("shutil.which", side_effect=lambda n: {"git": "/usr/bin/git", "java": None}.get(n)):
        result = doctor._check_java_toolchain()
    assert result.ok is False
    assert "not on PATH" in result.detail


def test_java_toolchain_fails_on_old_version(tmp_path, monkeypatch):
    # Adversarial: java present but below the 17 floor javalint compiles to ->
    # fail here rather than as an opaque UnsupportedClassVersionError mid-lint.
    monkeypatch.chdir(tmp_path)
    _java_repo(tmp_path)
    monkeypatch.setattr(doctor, "_java_major_version", lambda java: 11)
    with mock.patch("shutil.which", side_effect=lambda n: {"git": "/usr/bin/git", "java": "/usr/bin/java"}.get(n)):
        result = doctor._check_java_toolchain()
    assert result.ok is False
    assert "17" in result.detail and "11" in result.detail


@pytest.mark.parametrize(
    "banner, major",
    [
        ('openjdk version "21.0.11" 2026-04-21 LTS\n', 21),
        ('openjdk version "17.0.8" 2023-07-18\n', 17),
        ('java version "1.8.0_401"\n', 8),
    ],
)
def test_java_major_version_parses_modern_and_legacy(monkeypatch, banner, major):
    class _Fake:
        stdout = ""

        def __init__(self, err):
            self.stderr = err

    # `java -version` writes to stderr; the parser must read it there.
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: _Fake(banner))
    assert doctor._java_major_version("/usr/bin/java") == major


# -- ast-grep check --------------------------------------------------------


def test_ast_grep_ok_when_pinned_version_present(monkeypatch):
    monkeypatch.setattr(doctor.scopes, "ast_grep_exe", lambda: "/usr/bin/ast-grep")

    class _P:
        stdout = f"ast-grep {doctor._AST_GREP_VERSION}\n"
        stderr = ""

    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: _P())
    r = doctor._check_ast_grep()
    assert r.ok and doctor._AST_GREP_VERSION in r.detail


def test_ast_grep_fails_when_absent(monkeypatch):
    monkeypatch.setattr(doctor.scopes, "ast_grep_exe", lambda: None)
    r = doctor._check_ast_grep()
    assert not r.ok and "not found" in r.detail


def test_ast_grep_fails_on_version_drift(monkeypatch):
    # Adversarial: a grammar-bearing version bump can silently shift resolved
    # scope chains (A7); the pin must fail loudly, not pass on any ast-grep.
    monkeypatch.setattr(doctor.scopes, "ast_grep_exe", lambda: "/usr/bin/ast-grep")

    class _P:
        stdout = "ast-grep 0.45.0\n"
        stderr = ""

    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: _P())
    r = doctor._check_ast_grep()
    assert not r.ok
    assert "0.45.0" in r.detail and doctor._AST_GREP_VERSION in r.detail


# -- engines-store check ---------------------------------------------------


def _seed_store(tmp_path) -> Path:
    payload = tmp_path / "store"
    (payload / "bin").mkdir(parents=True)
    (payload / "bin" / "node").write_bytes(b"node\n")
    (payload / "vendor").mkdir()
    (payload / "vendor" / "x.js").write_bytes(b"x\n")
    return payload


def test_engines_store_dev_mode_skipped(monkeypatch):
    monkeypatch.setattr(engines_mod, "is_hermetic", lambda: False)
    r = doctor._check_engines_store()
    assert r.ok and "skipped" in r.detail


def test_engines_store_ok_when_tree_matches_pin(tmp_path, monkeypatch):
    payload = _seed_store(tmp_path)
    monkeypatch.setattr(engines_mod, "is_hermetic", lambda: True)
    monkeypatch.setenv("TACKBOX_ENGINES_DIR", str(payload))
    monkeypatch.setattr(
        engines_mod, "load_engines_json", lambda: {"store_sha256": sha256_tree(payload)}
    )
    r = doctor._check_engines_store()
    assert r.ok and r.check_id == "engines-store"


def test_engines_store_fails_on_tree_mismatch(tmp_path, monkeypatch):
    payload = _seed_store(tmp_path)
    monkeypatch.setattr(engines_mod, "is_hermetic", lambda: True)
    monkeypatch.setenv("TACKBOX_ENGINES_DIR", str(payload))
    monkeypatch.setattr(
        engines_mod, "load_engines_json", lambda: {"store_sha256": "cc" * 32}
    )
    r = doctor._check_engines_store()
    assert not r.ok
    assert "mismatch" in r.detail


def test_engines_store_reports_ensure_failure(monkeypatch):
    monkeypatch.setattr(engines_mod, "is_hermetic", lambda: True)
    monkeypatch.delenv("TACKBOX_ENGINES_DIR", raising=False)

    def boom(fetcher=None):
        raise engines_mod.EnginesStoreError("cannot download https://pypi.org/...: offline")

    monkeypatch.setattr(engines_mod, "ensure_engines", boom)
    r = doctor._check_engines_store()
    assert not r.ok
    assert "https://pypi.org" in r.detail

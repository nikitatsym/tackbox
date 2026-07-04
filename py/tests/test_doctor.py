from __future__ import annotations

import io
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from tackbox import doctor
from tackbox import engines as engines_mod


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
    assert lines[-1].startswith("doctor: 5 checks, 0 failed")
    ids = {ln.split(" ", 2)[1].rstrip(":") for ln in lines[:-1]}
    assert ids == {
        "platform",
        "payload-checksums",
        "binaries-start",
        "git-in-path",
        "go-toolchain",
    }
    assert all(ln.startswith("ok ") for ln in lines[:-1])


def test_dev_mode_skips_payload_and_binaries():
    _needs_git()
    with mock.patch.object(engines_mod, "is_hermetic", return_value=False):
        out = io.StringIO()
        doctor.run(out)
    text = out.getvalue()
    assert "ok payload-checksums: skipped (dev mode)" in text
    assert "ok binaries-start: skipped (dev mode)" in text


def _foreign_platform_key() -> str:
    """Any supported key guaranteed to differ from the host."""
    system = sys.platform
    if system.startswith("linux"):
        system = "linux"
    elif system.startswith("win") or system == "cygwin":
        system = "windows"
    host = doctor._SUPPORTED_PLATFORMS.get((system, platform.machine().lower()))
    return "linux-x86_64" if host != "linux-x86_64" else "macos-aarch64"


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
    monkeypatch.setattr(
        engines_mod, "hermetic_engines_root", lambda: tmp_path / "tackbox_engines"
    )
    (tmp_path / "tackbox_engines" / "bin").mkdir(parents=True)

    out = io.StringIO()
    rc = doctor.run(out)
    assert rc == 1
    text = out.getvalue()
    assert "fail platform:" in text
    assert f"wheel built for {_foreign_platform_key()}" in text
    assert "doctor: 5 checks, " in text


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
    (pkg / "engines.json").write_text(json.dumps(engines_json))
    monkeypatch.setattr(engines_mod, "_TACKBOX_PKG_ROOT", pkg)
    monkeypatch.setattr(engines_mod, "is_hermetic", lambda: True)
    monkeypatch.setattr(
        engines_mod, "hermetic_engines_root", lambda: tmp_path / "tackbox_engines"
    )
    (tmp_path / "tackbox_engines" / "bin").mkdir(parents=True)
    monkeypatch.setattr(engines_mod, "hermetic_env", lambda base=None: dict(base or {}))

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
    (pkg / "engines.json").write_text(json.dumps(engines_json))
    monkeypatch.setattr(engines_mod, "_TACKBOX_PKG_ROOT", pkg)
    monkeypatch.setattr(engines_mod, "is_hermetic", lambda: True)
    monkeypatch.setattr(
        engines_mod, "hermetic_engines_root", lambda: tmp_path / "tackbox_engines"
    )
    (tmp_path / "tackbox_engines" / "bin").mkdir(parents=True)
    monkeypatch.setattr(engines_mod, "hermetic_env", lambda base=None: dict(base or {}))

    out = io.StringIO()
    doctor.run(out)
    text = out.getvalue()
    assert "fail payload-checksums: missing=1" in text


def test_go_toolchain_ok_when_source_set_has_no_go(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "hello.py").write_text("print('hi')\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=tmp_path, check=True, capture_output=True,
    )

    with mock.patch("shutil.which", side_effect=lambda name: {"git": "/usr/bin/git", "go": None}.get(name)):
        result = doctor._check_go_toolchain()
    assert result.ok is True
    assert "not needed" in result.detail

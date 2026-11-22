"""Binary staging preserves content, executable modes, and NDK symlinks; the
Windows opengrep libraries ship with the license texts their inventory names."""

from __future__ import annotations

import dataclasses
import io
import json
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import build_wheels  # noqa: E402

_OPENGREP_LICENSE = {"licenses": {"opengrep": {"spdx": "LGPL-2.1", "notice": "opengrep"}}}


def _tgz(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def test_stage_binary_extracts_an_archive_member_executable(tmp_path, monkeypatch):
    archive = _tgz(tmp_path / "jscpd-linux-x64-gnu-5.0.12.tgz", {"package/bin/jscpd": b"jscpd\n"})
    monkeypatch.setattr(build_wheels, "fetch", lambda url, sha: archive)
    dest = tmp_path / "out" / "jscpd"
    entry = {"source_url": "u", "archive_sha256": "s", "archive_member": "package/bin/jscpd"}
    build_wheels.stage_binary(None, entry, dest)
    assert dest.read_bytes() == b"jscpd\n"
    if sys.platform != "win32":
        assert dest.stat().st_mode & stat.S_IXUSR


def test_stage_binary_puts_archive_libraries_beside_the_member(tmp_path, monkeypatch):
    archive = tmp_path / "opengrep_windows_x86.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("opengrep.exe", b"exe\n")
        zf.writestr("libgmp-10.dll", b"gmp\n")
        zf.writestr("libzstd-1.dll", b"zstd\n")
    monkeypatch.setattr(build_wheels, "fetch", lambda url, sha: archive)
    dest = tmp_path / "out" / "opengrep.exe"
    entry = {"source_url": "u", "archive_sha256": "s", "archive_member": "opengrep.exe",
             "archive_libraries": ["libgmp-10.dll", "libzstd-1.dll"]}
    build_wheels.stage_binary(None, entry, dest)
    assert {p.name: p.read_bytes() for p in dest.parent.iterdir()} == {
        "opengrep.exe": b"exe\n", "libgmp-10.dll": b"gmp\n", "libzstd-1.dll": b"zstd\n",
    }


def _windows_opengrep(tmp_path, monkeypatch, dll: bytes) -> tuple[build_wheels.Platform, Path]:
    archive = tmp_path / "opengrep_windows_x86.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("opengrep.exe", b"exe\n")
        zf.writestr("libgmp-10.dll", dll)
    monkeypatch.setattr(build_wheels, "fetch", lambda url, sha: archive)
    licenses = tmp_path / "engines" / "licenses" / "opengrep-windows"
    licenses.mkdir(parents=True)
    for text in ("NOTICE.txt", "gmp.COPYING.LESSERv3.txt", "gmp.COPYINGv3.txt", "runtime.txt"):
        (licenses / text).write_text(f"{text}\n")
    (licenses / "inventory.json").write_text(json.dumps({"components": [{
        "id": "libgmp-10.dll", "sha256": build_wheels.sha256_bytes(b"gmp\n"), "version": "6.3.0-1",
        "license": "LGPL-3.0-or-later", "license_path": "gmp.COPYING.LESSERv3.txt",
        "additional_license_paths": ["gmp.COPYINGv3.txt"], "notice_path": "runtime.txt",
        "source": "https://example.invalid/gmp-src.tar.zst",
    }]}))
    pl = build_wheels.platform_for(build_wheels.load_manifest(), "windows-x86_64")
    monkeypatch.setattr(build_wheels, "ENGINES_DIR", tmp_path / "engines")
    opengrep = {**pl.opengrep, "archive_libraries": ["libgmp-10.dll"]}
    return dataclasses.replace(pl, opengrep=opengrep), tmp_path / "og"


def test_prepare_opengrep_ships_library_licenses(tmp_path, monkeypatch):
    pl, root = _windows_opengrep(tmp_path, monkeypatch, b"gmp\n")
    entries = build_wheels.prepare_opengrep(pl, _OPENGREP_LICENSE, root)
    prefix = "tackbox_opengrep/third_party/licenses/opengrep-windows"
    assert entries[1] == {
        "id": "opengrep/libgmp-10.dll", "kind": "library", "version": "6.3.0-1",
        "source_url": pl.opengrep["source_url"], "archive_sha256": pl.opengrep["archive_sha256"],
        "sha256": build_wheels.sha256_bytes(b"gmp\n"), "path": "tackbox_opengrep/bin/libgmp-10.dll",
        "license": "LGPL-3.0-or-later", "license_path": f"{prefix}/gmp.COPYING.LESSERv3.txt",
        "notice_path": f"{prefix}/runtime.txt", "source": "https://example.invalid/gmp-src.tar.zst",
    }
    assert sorted(p.name for p in (root / "third_party" / "licenses" / "opengrep-windows").iterdir()) == [
        "NOTICE.txt", "gmp.COPYING.LESSERv3.txt", "gmp.COPYINGv3.txt", "inventory.json", "runtime.txt",
    ]


def test_prepare_opengrep_refuses_a_library_build_without_its_inventory_entry(tmp_path, monkeypatch):
    pl, root = _windows_opengrep(tmp_path, monkeypatch, b"another gmp build\n")
    with pytest.raises(SystemExit, match="libgmp-10.dll"):
        build_wheels.prepare_opengrep(pl, _OPENGREP_LICENSE, root)


def test_stage_binary_copies_a_bare_download(tmp_path, monkeypatch):
    raw = tmp_path / "opengrep_linux_x86"
    raw.write_bytes(b"binary\n")
    monkeypatch.setattr(build_wheels, "fetch", lambda url, sha: raw)
    dest = tmp_path / "out" / "opengrep"
    build_wheels.stage_binary(None, {"source_url": "u", "archive_sha256": "s"}, dest)
    assert dest.read_bytes() == b"binary\n"
    if sys.platform != "win32":
        assert dest.stat().st_mode & stat.S_IXUSR


def test_stage_binary_builds_a_cargo_entry(tmp_path, monkeypatch):
    built = tmp_path / "built-jscpd"
    built.write_bytes(b"elf\n")
    calls = []
    monkeypatch.setattr(build_wheels, "cargo_build", lambda pl, entry: calls.append(entry) or built)
    dest = tmp_path / "out" / "jscpd"
    entry = {"cargo_bin": "jscpd", "source_url": "u", "archive_sha256": "s"}
    build_wheels.stage_binary(None, entry, dest)
    assert calls == [entry]
    assert dest.read_bytes() == b"elf\n"


@pytest.mark.skipif(sys.platform == "win32", reason="the NDK unpacks on its linux-x86_64 host only")
def test_extract_zip_tree_keeps_modes_and_symlinks(tmp_path):
    archive = tmp_path / "ndk.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        tool = zipfile.ZipInfo("ndk/bin/clang-18")
        tool.external_attr = (stat.S_IFREG | 0o755) << 16
        zf.writestr(tool, b"clang\n")
        link = zipfile.ZipInfo("ndk/bin/clang")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, b"clang-18")
    build_wheels.extract_zip_tree(archive, tmp_path / "out")
    tool_path = tmp_path / "out" / "ndk" / "bin" / "clang-18"
    assert stat.S_IMODE(tool_path.stat().st_mode) == 0o755
    link_path = tmp_path / "out" / "ndk" / "bin" / "clang"
    assert link_path.is_symlink() and link_path.read_bytes() == b"clang\n"


def test_cargo_build_refuses_a_host_without_the_ndk(monkeypatch):
    monkeypatch.setattr(build_wheels, "detect_platform_key", lambda: "macos-aarch64")
    pl = build_wheels.platform_for(build_wheels.load_manifest(), "android-aarch64")
    with pytest.raises(SystemExit, match="linux-x86_64"):
        build_wheels.cargo_build(pl, pl.jscpd)



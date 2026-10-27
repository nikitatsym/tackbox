"""scripts/build_wheels.py staging helpers and the manifest's Android entry.

The full host build runs in test_wheels_e2e.py; the Android build needs the
pinned NDK and runs in CI (wheels.yml), so here the pieces it is made of are
pinned without a toolchain.
"""

from __future__ import annotations

import io
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from tackbox import doctor

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import build_wheels  # noqa: E402


def _tgz(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def test_stage_binary_extracts_an_archive_member_executable(tmp_path, monkeypatch):
    archive = _tgz(tmp_path / "opengrep-core_linux_aarch64.tar.gz", {"opengrep-core": b"core\n"})
    monkeypatch.setattr(build_wheels, "fetch", lambda url, sha: archive)
    dest = tmp_path / "out" / "opengrep"
    entry = {"source_url": "u", "archive_sha256": "s", "archive_member": "opengrep-core"}
    build_wheels.stage_binary(None, entry, dest)
    assert dest.read_bytes() == b"core\n"
    assert dest.stat().st_mode & stat.S_IXUSR


def test_stage_binary_copies_a_bare_download(tmp_path, monkeypatch):
    raw = tmp_path / "opengrep_manylinux_x86"
    raw.write_bytes(b"binary\n")
    monkeypatch.setattr(build_wheels, "fetch", lambda url, sha: raw)
    dest = tmp_path / "out" / "opengrep"
    build_wheels.stage_binary(None, {"source_url": "u", "archive_sha256": "s"}, dest)
    assert dest.read_bytes() == b"binary\n"
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


def test_android_manifest_entry_is_consistent():
    """Android: wheel tag at the NDK API level, no bundled node, Rust tools
    built from their crates, and the bundled ast-grep at the pin setup.py drops
    for Android and doctor gates."""
    manifest = build_wheels.load_manifest()
    pl = build_wheels.platform_for(manifest, "android-aarch64")
    assert pl.wheel_plat == f"android_{pl.cargo['android_api']}_arm64_v8a"
    assert pl.cargo["target"] == "aarch64-linux-android"
    assert pl.node is None
    assert "cargo_bin" in pl.jscpd and "cargo_bin" in pl.ast_grep
    assert pl.ast_grep["version"] == doctor._AST_GREP_VERSION
    setup = (REPO / "py" / "setup.py").read_text(encoding="utf-8")
    assert f"'ast-grep-cli=={doctor._AST_GREP_VERSION}; sys_platform != \"android\"'" in setup
    linux = manifest["platforms"]["linux-aarch64"]
    assert pl.jscpd["version"] == linux["jscpd"]["version"]
    assert pl.opengrep["version"] == linux["opengrep"]["version"]
    assert "ast-grep" in manifest["licenses"]


def test_host_platforms_bundle_node_and_build_nothing():
    manifest = build_wheels.load_manifest()
    for key in manifest["platforms"]:
        if key == "android-aarch64":
            continue
        pl = build_wheels.platform_for(manifest, key)
        assert pl.node is not None and pl.cargo is None and pl.ast_grep is None, key
        assert not any("cargo_bin" in e for e in (pl.node, pl.jscpd, pl.opengrep)), key

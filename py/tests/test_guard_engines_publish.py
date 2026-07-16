"""Pre-publish fat-payload integrity guard (scripts/guard_engines_publish.py).

publish-fat uploads with skip-existing: true, so a re-run under an unchanged
engines/VERSION skips the upload. That is safe only while the payload is byte-
identical: thin pins store_sha256 = the fat payload tree-sha, and install-time
verification refuses a fetched fat whose tree-sha differs. If a fat input changes
without an engines/VERSION bump, skip-existing keeps the OLD fat on PyPI while the
new thin ships pointing at the NEW tree-sha - every fresh install then bricks.

The guard compares each local fat wheel against its same-platform already-
published wheel (matched by exact filename, the skip-existing key) using
engines_payload_tree_sha256 - the exact digest install-time verification uses.
These tests drive the pure compare core and the driver with no network.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import guard_engines_publish as guard  # noqa: E402

VERSION = "1.0.0"
MAC = f"tackbox_engines-{VERSION}-py3-none-macosx_11_0_arm64.whl"
LINUX = f"tackbox_engines-{VERSION}-py3-none-manylinux_2_28_x86_64.whl"

# One representative file per payload subtree; the platform-specific bytes are in
# bin/ (node/opengrep), which is exactly what differs across platform wheels.
_PAYLOAD = {
    "bin/node": b"#!/bin/sh\necho node\n",
    "bin/opengrep": b"opengrep-binary-bytes\n",
    "vendor/node_modules/eslint/index.js": b"module.exports = {}\n",
    "third_party/licenses/node.LICENSE.txt": b"MIT\n",
}


def _build_fat_wheel(
    dest_dir: Path,
    name: str,
    payload: dict[str, bytes],
    *,
    reverse: bool = False,
    date_time: tuple[int, int, int, int, int, int] = (1980, 1, 1, 0, 0, 0),
) -> Path:
    """A minimal fat wheel: a zip whose members live under tackbox_engines/.
    `reverse`/`date_time` vary only the container (member order, timestamps) so a
    same-payload wheel can be built with different bytes - proving the guard pins
    the payload tree, not the container (rebuilds are not zip-reproducible)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    wheel = dest_dir / name
    with zipfile.ZipFile(wheel, "w") as zf:
        for rel, content in sorted(payload.items(), reverse=reverse):
            info = zipfile.ZipInfo(f"tackbox_engines/{rel}", date_time=date_time)
            info.external_attr = 0o644 << 16
            zf.writestr(info, content)
    return wheel


def _changed(payload: dict[str, bytes]) -> dict[str, bytes]:
    out = dict(payload)
    out["bin/opengrep"] = out["bin/opengrep"] + b"tampered\n"
    return out


# -- pure compare core (no network) ---------------------------------------


def test_absent_publish_is_fresh(tmp_path):
    local = _build_fat_wheel(tmp_path, MAC, _PAYLOAD)
    result = guard.compare_fat_wheel(local, None)
    assert result.status == guard.FRESH
    assert result.published_sha is None
    assert result.local_sha


def test_identical_payload_different_container_matches(tmp_path):
    local = _build_fat_wheel(tmp_path / "local", MAC, _PAYLOAD)
    # Same payload, deliberately different container (reversed member order,
    # different timestamps) - the pin must ignore container bytes.
    published = _build_fat_wheel(
        tmp_path / "pub", MAC, _PAYLOAD, reverse=True, date_time=(2021, 6, 7, 8, 9, 10)
    )
    result = guard.compare_fat_wheel(local, published)
    assert result.status == guard.MATCH
    assert result.local_sha == result.published_sha


def test_changed_payload_mismatches(tmp_path):
    local = _build_fat_wheel(tmp_path / "local", MAC, _PAYLOAD)
    published = _build_fat_wheel(tmp_path / "pub", MAC, _changed(_PAYLOAD))
    result = guard.compare_fat_wheel(local, published)
    assert result.status == guard.MISMATCH
    assert result.local_sha != result.published_sha


# -- driver over local wheels (injected seams) ----------------------------


def _fat_dir(tmp_path: Path, wheels: dict[str, dict[str, bytes]]) -> Path:
    d = tmp_path / "fat"
    d.mkdir()
    for name, payload in wheels.items():
        _build_fat_wheel(d, name, payload)
    return d


def _http_get_for(version: str, filenames: list[str]):
    index = {
        "releases": {
            version: [
                {"filename": n, "url": f"https://files.pythonhosted.org/{n}", "digests": {}}
                for n in filenames
            ]
        }
    }
    body = json.dumps(index).encode()

    def http_get(url: str) -> tuple[int, bytes]:
        assert url == guard.PYPI_ENGINES_JSON
        return 200, body

    return http_get


def _download_from(mapping: dict[str, Path]):
    def download(entry: dict, workdir: Path) -> Path:
        return mapping[entry["filename"]]

    return download


def test_guard_wheels_fresh_on_404(tmp_path):
    fat = _fat_dir(tmp_path, {MAC: _PAYLOAD})

    def http_get(url: str) -> tuple[int, bytes]:
        return 404, b""

    def download(entry, workdir):
        raise AssertionError("download must not run when nothing is published")

    results = guard.guard_wheels(fat, VERSION, tmp_path, http_get=http_get, download=download)
    assert [r.status for r in results] == [guard.FRESH]


def test_guard_wheels_fresh_when_version_absent(tmp_path):
    fat = _fat_dir(tmp_path, {MAC: _PAYLOAD})
    # Package exists on PyPI, but not this version -> nothing to verify.
    http_get = _http_get_for("0.9.9", [MAC])

    def download(entry, workdir):
        raise AssertionError("download must not run for an unpublished version")

    results = guard.guard_wheels(fat, VERSION, tmp_path, http_get=http_get, download=download)
    assert [r.status for r in results] == [guard.FRESH]


def test_guard_wheels_match_when_published_identical(tmp_path):
    fat = _fat_dir(tmp_path, {MAC: _PAYLOAD})
    published = _build_fat_wheel(tmp_path / "pub", MAC, _PAYLOAD, reverse=True)
    results = guard.guard_wheels(
        fat, VERSION, tmp_path,
        http_get=_http_get_for(VERSION, [MAC]),
        download=_download_from({MAC: published}),
    )
    assert [r.status for r in results] == [guard.MATCH]


def test_guard_wheels_per_platform_mismatch_fails_even_if_other_matches(tmp_path):
    """The per-platform contract: each local wheel is compared only against its
    same-platform published wheel. A payload change on ONE platform must fail
    even while another platform's wheel is byte-identical."""
    fat = _fat_dir(tmp_path, {MAC: _PAYLOAD, LINUX: _PAYLOAD})
    pub_mac = _build_fat_wheel(tmp_path / "pub", MAC, _PAYLOAD, reverse=True)
    pub_linux = _build_fat_wheel(tmp_path / "pub", LINUX, _changed(_PAYLOAD))
    results = guard.guard_wheels(
        fat, VERSION, tmp_path,
        http_get=_http_get_for(VERSION, [MAC, LINUX]),
        download=_download_from({MAC: pub_mac, LINUX: pub_linux}),
    )
    by_name = {r.wheel: r.status for r in results}
    assert by_name[MAC] == guard.MATCH
    assert by_name[LINUX] == guard.MISMATCH


def test_guard_wheels_no_local_wheels_hard_errors(tmp_path):
    empty = tmp_path / "fat"
    empty.mkdir()
    with pytest.raises(SystemExit) as ei:
        guard.guard_wheels(empty, VERSION, tmp_path, http_get=_http_get_for(VERSION, []))
    assert "tackbox_engines-" in str(ei.value)


def test_published_release_files_hard_errors_on_5xx():
    def http_get(url: str) -> tuple[int, bytes]:
        return 503, b"upstream unavailable"

    with pytest.raises(SystemExit) as ei:
        guard.published_release_files(VERSION, http_get=http_get)
    assert "503" in str(ei.value)


def test_default_download_rejects_digest_mismatch(tmp_path, monkeypatch):
    entry = {
        "filename": MAC,
        "url": f"https://files.pythonhosted.org/{MAC}",
        "digests": {"sha256": "cc" * 32},
    }
    monkeypatch.setattr(guard, "urlopen", lambda url, timeout=None: io.BytesIO(b"body"))
    with pytest.raises(SystemExit) as ei:
        guard._default_download(entry, tmp_path)
    assert "cc" * 32 in str(ei.value)


# -- main exit codes (seams monkeypatched, no network) --------------------


def test_main_returns_0_when_all_ok(tmp_path, monkeypatch, capsys):
    fat = _fat_dir(tmp_path, {MAC: _PAYLOAD})
    published = _build_fat_wheel(tmp_path / "pub", MAC, _PAYLOAD, reverse=True)
    monkeypatch.setattr(guard, "_default_http_get", _http_get_for(VERSION, [MAC]))
    monkeypatch.setattr(guard, "_default_download", _download_from({MAC: published}))
    rc = guard.main(["--fat-dir", str(fat), "--version", VERSION])
    assert rc == 0
    assert "ok" in capsys.readouterr().out


def test_main_returns_1_on_mismatch(tmp_path, monkeypatch, capsys):
    fat = _fat_dir(tmp_path, {MAC: _PAYLOAD})
    published = _build_fat_wheel(tmp_path / "pub", MAC, _changed(_PAYLOAD))
    monkeypatch.setattr(guard, "_default_http_get", _http_get_for(VERSION, [MAC]))
    monkeypatch.setattr(guard, "_default_download", _download_from({MAC: published}))
    rc = guard.main(["--fat-dir", str(fat), "--version", VERSION])
    assert rc == 1
    err = capsys.readouterr().err
    assert "engines/VERSION was not bumped" in err

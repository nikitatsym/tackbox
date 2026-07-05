"""F6: the machine-versioned engines store (fetch, verify, atomic install).

The store replaces the tackbox-engines pip dependency. The thin wheel no
longer ships fat as a Requires-Dist; instead `ensure_engines()` resolves the
fat wheel from PyPI once per engines version, pins it by sha256, unpacks it
to `$XDG_DATA_HOME/tackbox/engines/<version>/`, and reuses that copy across
every thin version.

Every test here injects a `file://`-style fetcher so no test touches the
network; the default PyPI fetcher is exercised separately with a stubbed
urlopen. XDG_DATA_HOME is redirected per test so nothing writes into the
developer's real `~/.local/share/tackbox`.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from tackbox import engines
from tackbox.cache import sha256_file, sha256_tree


# -- fixture wheel builder -------------------------------------------------

# One representative file per payload subtree. bin/node carries the exec bit
# so the unpack-restores-mode assertion has something to check.
_PAYLOAD = {
    "bin/node": b"#!/bin/sh\necho node\n",
    "bin/opengrep": b"opengrep-binary-bytes\n",
    "vendor/node_modules/eslint/index.js": b"module.exports = {}\n",
    "vendor/node_modules/.bin/eslint": b"#!/bin/sh\n",
    "third_party/licenses/node.LICENSE.txt": b"MIT\n",
}
_EXEC = {"bin/node", "bin/opengrep", "vendor/node_modules/.bin/eslint"}


def _detected_platform() -> str:
    key = engines.detect_platform_key()
    assert key is not None, "test host is not a supported tackbox platform"
    return key


def _build_fat_wheel(dest_dir: Path, version: str, payload: dict[str, bytes]) -> Path:
    """A minimal fat wheel: a zip whose members live under `tackbox_engines/`.

    Only the payload subtree matters to the unpacker; no dist-info is needed.
    Members in `_EXEC` get mode 0o755 recorded so the unpacker can restore it.
    """
    plat = _detected_platform()
    name = f"tackbox_engines-{version}-py3-none-{plat.replace('-', '_')}.whl"
    wheel = dest_dir / name
    with zipfile.ZipFile(wheel, "w") as zf:
        for rel, content in sorted(payload.items()):
            info = zipfile.ZipInfo(f"tackbox_engines/{rel}")
            mode = 0o755 if rel in _EXEC else 0o644
            info.external_attr = mode << 16
            zf.writestr(info, content)
    return wheel


def _expected_store_sha(payload: dict[str, bytes], tmp: Path) -> str:
    """sha256_tree of the payload as it will appear unpacked in the store."""
    src = tmp / "expected-store"
    for rel, content in payload.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return sha256_tree(src)


def _engines_json(
    version: str,
    wheel: Path,
    store_sha: str,
    *,
    platform: str | None = None,
    wheel_sha: str | None = None,
) -> dict:
    plat = platform or _detected_platform()
    return {
        "schema": 1,
        "engines_version": version,
        "payload_sha256": "unused-in-store-tests",
        "platform": plat,
        "wheel_plat": plat.replace("-", "_"),
        "fat_wheel": {
            "platform": plat,
            "wheel": wheel.name,
            "sha256": wheel_sha if wheel_sha is not None else sha256_file(wheel),
        },
        "store_sha256": store_sha,
        "engines": [],
    }


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    """Redirect the store base to a tmp dir and clear the override.

    Returns a small namespace with the crafted engines.json and a fetcher
    factory that copies the fixture wheel into the caller-provided workdir
    (mimicking a download) and counts its calls.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("TACKBOX_ENGINES_DIR", raising=False)

    version = "0.9.0"
    payload = dict(_PAYLOAD)
    wheel = _build_fat_wheel(tmp_path, version, payload)
    store_sha = _expected_store_sha(payload, tmp_path)
    data = _engines_json(version, wheel, store_sha)
    monkeypatch.setattr(engines, "load_engines_json", lambda: data)

    calls = {"n": 0}

    def fetcher(engines_json: dict, workdir: Path) -> Path:
        calls["n"] += 1
        out = workdir / wheel.name
        out.write_bytes(wheel.read_bytes())
        return out

    class NS:
        pass

    ns = NS()
    ns.version = version
    ns.wheel = wheel
    ns.data = data
    ns.store_sha = store_sha
    ns.payload = payload
    ns.fetcher = fetcher
    ns.calls = calls
    ns.store_dir = tmp_path / "xdg" / "tackbox" / "engines" / version
    ns.tmp = tmp_path
    return ns


# -- ensure: happy path ----------------------------------------------------


def test_ensure_absent_fetches_verifies_and_atomically_installs(store_env):
    root = engines.ensure_engines(fetcher=store_env.fetcher)
    assert root == store_env.store_dir
    assert root.is_dir()
    assert store_env.calls["n"] == 1
    # Whole payload landed with the right contents.
    for rel, content in store_env.payload.items():
        assert (root / rel).read_bytes() == content
    # The unpacked tree matches the pin exactly.
    assert sha256_tree(root) == store_env.store_sha


def test_ensure_restores_executable_bit_on_binaries(store_env):
    root = engines.ensure_engines(fetcher=store_env.fetcher)
    for rel in _EXEC:
        assert os.access(root / rel, os.X_OK), f"{rel} lost its exec bit through the store"
    assert not os.access(
        root / "third_party/licenses/node.LICENSE.txt", os.X_OK
    ), "non-exec payload file must not gain +x"


def test_existing_store_does_not_call_fetcher(store_env):
    def explode(engines_json, workdir):
        raise AssertionError("fetcher must not run when the store already exists")

    store_env.store_dir.mkdir(parents=True)
    (store_env.store_dir / "bin").mkdir()
    (store_env.store_dir / "bin" / "node").write_bytes(b"already here\n")
    root = engines.ensure_engines(fetcher=explode)
    assert root == store_env.store_dir


def test_double_ensure_yields_one_valid_store(store_env):
    first = engines.ensure_engines(fetcher=store_env.fetcher)
    second = engines.ensure_engines(fetcher=store_env.fetcher)
    assert first == second == store_env.store_dir
    assert store_env.calls["n"] == 1  # second call short-circuits on existence
    base = store_env.store_dir.parent
    versions = [p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")]
    assert versions == [store_env.version]


# -- ensure: override ------------------------------------------------------


def test_override_dir_short_circuits_fetch(store_env, monkeypatch, tmp_path):
    override = tmp_path / "unpacked-dist-fat"
    override.mkdir()
    monkeypatch.setenv("TACKBOX_ENGINES_DIR", str(override))

    def explode(engines_json, workdir):
        raise AssertionError("override must skip the fetcher entirely")

    root = engines.ensure_engines(fetcher=explode)
    assert root == override
    assert engines.hermetic_engines_root() == override


# -- ensure: adversarial (attack the guarantee) ---------------------------


def test_corrupt_wheel_sha_hard_errors_and_leaves_no_store(store_env, monkeypatch):
    # The pin says one thing; the wheel bytes hash to another. A compromised
    # PyPI serving a swapped wheel must be caught before unpack, and nothing
    # may be committed to the store.
    bad = dict(store_env.data)
    bad_fat = dict(bad["fat_wheel"])
    bad_fat["sha256"] = "aa" * 32
    bad["fat_wheel"] = bad_fat
    monkeypatch.setattr(engines, "load_engines_json", lambda: bad)

    with pytest.raises(engines.EnginesStoreError) as ei:
        engines.ensure_engines(fetcher=store_env.fetcher)
    msg = str(ei.value)
    assert "aa" * 32 in msg and "sha256" in msg
    assert not store_env.store_dir.exists()
    # No half-written temp siblings survive a failed fetch.
    base = store_env.store_dir.parent
    assert not base.exists() or list(base.iterdir()) == []


def test_corrupt_tree_after_unpack_hard_errors(store_env, monkeypatch):
    # Second barrier: the wheel matches its own sha pin, but the payload tree
    # does not match store_sha256 (a legitimately-signed but repacked wheel).
    # The post-unpack tree verify must still refuse it.
    bad = dict(store_env.data)
    bad["store_sha256"] = "bb" * 32
    monkeypatch.setattr(engines, "load_engines_json", lambda: bad)

    with pytest.raises(engines.EnginesStoreError) as ei:
        engines.ensure_engines(fetcher=store_env.fetcher)
    assert "tree" in str(ei.value) or "store_sha256" in str(ei.value) or "bb" * 32 in str(ei.value)
    assert not store_env.store_dir.exists()


def test_platform_drift_hard_errors_with_both_values_before_fetch(store_env, monkeypatch):
    # The Rosetta / wrong-wheel case: engines.json pins a fat wheel for a
    # different platform than the one we are running on. ensure must refuse
    # loudly, naming both platforms, and must never reach the fetcher.
    detected = _detected_platform()
    foreign = "linux-x86_64" if detected != "linux-x86_64" else "macos-aarch64"
    drift = dict(store_env.data)
    drift_fat = dict(drift["fat_wheel"])
    drift_fat["platform"] = foreign
    drift["fat_wheel"] = drift_fat
    drift["platform"] = foreign
    monkeypatch.setattr(engines, "load_engines_json", lambda: drift)

    def explode(engines_json, workdir):
        raise AssertionError("platform drift must be caught before any fetch")

    with pytest.raises(engines.EnginesStoreError) as ei:
        engines.ensure_engines(fetcher=explode)
    msg = str(ei.value)
    assert foreign in msg and detected in msg
    assert not store_env.store_dir.exists()


def test_fetch_failure_propagates_loudly_and_leaves_no_store(store_env):
    def failing(engines_json, workdir):
        raise engines.EnginesStoreError(
            "cannot download https://pypi.org/.../tackbox_engines.whl: timed out"
        )

    with pytest.raises(engines.EnginesStoreError) as ei:
        engines.ensure_engines(fetcher=failing)
    assert "https://pypi.org" in str(ei.value)
    assert not store_env.store_dir.exists()


# -- ensure: GC ------------------------------------------------------------


def test_gc_removes_stale_version_siblings_but_keeps_inflight(store_env):
    base = store_env.store_dir.parent
    base.mkdir(parents=True)
    stale = base / "0.1.0"
    stale.mkdir()
    (stale / "marker").write_text("old")
    inflight = base / ".ensure-someone-else"  # dot-prefixed: a concurrent fetch's tmp
    inflight.mkdir()
    (inflight / "partial").write_text("mid-unpack")

    engines.ensure_engines(fetcher=store_env.fetcher)

    assert store_env.store_dir.is_dir()
    assert not stale.exists(), "stale version sibling must be GC'd"
    assert inflight.exists(), "an in-flight (.dot) sibling must survive GC (no fratricide)"


# -- hermetic_engines_root -------------------------------------------------


def test_hermetic_engines_root_resolves_versioned_store(store_env):
    assert engines.hermetic_engines_root() == store_env.store_dir


def test_hermetic_engines_root_prefers_override(store_env, monkeypatch, tmp_path):
    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setenv("TACKBOX_ENGINES_DIR", str(override))
    assert engines.hermetic_engines_root() == override


# -- default PyPI fetcher (stubbed urlopen, no network) --------------------


def test_default_fetcher_resolves_wheel_by_pypi_json(store_env, monkeypatch):
    wheel_bytes = store_env.wheel.read_bytes()
    download_url = "https://files.pythonhosted.org/packages/ab/cd/" + store_env.wheel.name
    index = {
        "urls": [
            {"filename": "tackbox_engines-0.9.0-py3-none-other.whl", "url": "https://x/other"},
            {"filename": store_env.wheel.name, "url": download_url},
        ]
    }
    seen: list[str] = []

    def fake_urlopen(url, timeout=None):
        seen.append(url)
        if url.endswith("/json"):
            return io.BytesIO(json.dumps(index).encode())
        if url == download_url:
            return io.BytesIO(wheel_bytes)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(engines, "urlopen", fake_urlopen)
    workdir = store_env.tmp / "dl"
    workdir.mkdir()
    got = engines._download_fat_wheel(store_env.data, workdir)
    assert got.read_bytes() == wheel_bytes
    assert seen[0] == "https://pypi.org/pypi/tackbox-engines/0.9.0/json"
    assert download_url in seen


def test_default_fetcher_missing_wheel_in_index_hard_errors(store_env, monkeypatch):
    index = {"urls": [{"filename": "some-other-file.whl", "url": "https://x/y"}]}
    monkeypatch.setattr(
        engines, "urlopen", lambda url, timeout=None: io.BytesIO(json.dumps(index).encode())
    )
    workdir = store_env.tmp / "dl2"
    workdir.mkdir()
    with pytest.raises(engines.EnginesStoreError) as ei:
        engines._download_fat_wheel(store_env.data, workdir)
    assert store_env.wheel.name in str(ei.value)

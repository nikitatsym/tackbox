"""F6: the machine-versioned payload stores (fetch, verify, atomic install).

The thin wheel ships no payload as a Requires-Dist; instead `ensure_payload()`
resolves each payload wheel (tackbox-engines, tackbox-opengrep) from PyPI once
per payload version, pins it by its unpacked tree sha256, unpacks it to
`$XDG_DATA_HOME/tackbox/<payload key>/<version>/`, and reuses that copy
across every thin version.

Every test here injects a `file://`-style fetcher so no test touches the
network; the default PyPI fetcher is exercised separately with a stubbed
urlopen. XDG_DATA_HOME is redirected per test so nothing writes into the
developer's real `~/.local/share/tackbox`.
"""

from __future__ import annotations

import concurrent.futures
import io
import json
import os
import threading
import time
import zipfile
from pathlib import Path

import pytest

from tackbox import engines
from tackbox.hashing import sha256_tree


# -- fixture wheel builder -------------------------------------------------

# One representative file per payload subtree. bin/node carries the exec bit
# so the unpack-restores-mode assertion has something to check.
_PAYLOAD = {
    "bin/node": b"#!/bin/sh\necho node\n",
    "bin/jscpd": b"jscpd-binary-bytes\n",
    "vendor/node_modules/eslint/index.js": b"module.exports = {}\n",
    "vendor/node_modules/.bin/eslint": b"#!/bin/sh\n",
    "third_party/licenses/node.LICENSE.txt": b"MIT\n",
}
_EXEC = {"bin/node", "bin/jscpd", "vendor/node_modules/.bin/eslint"}

_OPENGREP_PAYLOAD = {
    "bin/opengrep": b"opengrep-binary-bytes\n",
    "third_party/licenses/opengrep.LICENSE.txt": b"LGPL-2.1\n",
}


def _detected_platform() -> str:
    key = engines.detect_platform_key()
    assert key is not None, "test host is not a supported tackbox platform"
    return key


def _build_payload_wheel(
    dest_dir: Path,
    version: str,
    payload: dict[str, bytes],
    package: str = "tackbox_engines",
) -> Path:
    """A minimal payload wheel: a zip whose members live under `<package>/`.

    Only the payload subtree matters to the unpacker; no dist-info is needed.
    Members in `_EXEC` (and bin/opengrep) get mode 0o755 recorded so the
    unpacker can restore it.
    """
    plat = _detected_platform()
    name = f"{package}-{version}-py3-none-{plat.replace('-', '_')}.whl"
    wheel = dest_dir / name
    with zipfile.ZipFile(wheel, "w") as zf:
        for rel, content in sorted(payload.items()):
            info = zipfile.ZipInfo(f"{package}/{rel}")
            mode = 0o755 if rel in _EXEC or rel == "bin/opengrep" else 0o644
            info.external_attr = mode << 16
            zf.writestr(info, content)
    return wheel


def _expected_store_sha(payload: dict[str, bytes], tmp: Path) -> str:
    """sha256_tree of the payload as it will appear unpacked in the store."""
    src = tmp / f"expected-store-{len(list(tmp.glob('expected-store-*')))}"
    for rel, content in payload.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return sha256_tree(src)


def _pin(payload: engines.Payload, version: str, wheel: Path, store_sha: str) -> dict:
    return {
        "project": payload.project,
        "version": version,
        "wheel": wheel.name,
        "store_sha256": store_sha,
    }


def _engines_json(pins: dict[str, dict], *, platform: str | None = None) -> dict:
    plat = platform or _detected_platform()
    return {
        "payload_sha256": "unused-in-store-tests",
        "platform": plat,
        "wheel_plat": plat.replace("-", "_"),
        "payloads": pins,
        "engines": [],
    }


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    """Redirect the store base to a tmp dir and clear the overrides.

    Returns a small namespace with the crafted engines.json (pins for both
    payloads) and a fetcher that copies the engines fixture wheel into the
    caller-provided workdir (mimicking a download) and counts its calls.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("TACKBOX_ENGINES_DIR", raising=False)
    monkeypatch.delenv("TACKBOX_OPENGREP_DIR", raising=False)

    version = "0.9.0"
    payload = dict(_PAYLOAD)
    wheel = _build_payload_wheel(tmp_path, version, payload)
    store_sha = _expected_store_sha(payload, tmp_path)
    pin = _pin(engines.ENGINES, version, wheel, store_sha)

    og_version = "0.3.0"
    og_wheel = _build_payload_wheel(tmp_path, og_version, _OPENGREP_PAYLOAD, "tackbox_opengrep")
    og_pin = _pin(
        engines.OPENGREP, og_version, og_wheel, _expected_store_sha(_OPENGREP_PAYLOAD, tmp_path)
    )

    data = _engines_json({"engines": pin, "opengrep": og_pin})
    monkeypatch.setattr(engines, "load_engines_json", lambda: data)

    calls = {"n": 0}

    def fetcher(pin_arg: dict, workdir: Path) -> Path:
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
    ns.pin = pin
    ns.store_sha = store_sha
    ns.payload = payload
    ns.fetcher = fetcher
    ns.calls = calls
    ns.store_dir = tmp_path / "xdg" / "tackbox" / "engines" / version
    ns.og_wheel = og_wheel
    ns.og_pin = og_pin
    ns.og_store_dir = tmp_path / "xdg" / "tackbox" / "opengrep" / og_version
    ns.tmp = tmp_path
    return ns


def _ensure_engines(fetcher) -> Path:
    return engines.ensure_payload(engines.ENGINES, fetcher=fetcher)


# -- ensure: happy path ----------------------------------------------------


def test_ensure_absent_fetches_verifies_and_atomically_installs(store_env):
    root = _ensure_engines(store_env.fetcher)
    assert root == store_env.store_dir
    assert root.is_dir()
    assert store_env.calls["n"] == 1
    # Whole payload landed with the right contents.
    for rel, content in store_env.payload.items():
        assert (root / rel).read_bytes() == content
    # The unpacked tree matches the pin exactly.
    assert sha256_tree(root) == store_env.store_sha


@pytest.mark.skipif(
    os.name == "nt", reason="Windows does not preserve POSIX executable bits"
)
def test_ensure_restores_executable_bit_on_binaries(store_env):
    root = _ensure_engines(store_env.fetcher)
    for rel in _EXEC:
        assert os.access(root / rel, os.X_OK), f"{rel} lost its exec bit through the store"
    assert not os.access(
        root / "third_party/licenses/node.LICENSE.txt", os.X_OK
    ), "non-exec payload file must not gain +x"


def test_existing_store_does_not_call_fetcher(store_env):
    def explode(pin, workdir):
        raise AssertionError("fetcher must not run when the store already exists")

    store_env.store_dir.mkdir(parents=True)
    (store_env.store_dir / "bin").mkdir()
    (store_env.store_dir / "bin" / "node").write_bytes(b"already here\n")
    root = _ensure_engines(explode)
    assert root == store_env.store_dir


def test_double_ensure_yields_one_valid_store(store_env):
    first = _ensure_engines(store_env.fetcher)
    second = _ensure_engines(store_env.fetcher)
    assert first == second == store_env.store_dir
    assert store_env.calls["n"] == 1  # second call short-circuits on existence
    base = store_env.store_dir.parent
    versions = [p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")]
    assert versions == [store_env.version]


def test_concurrent_ensure_fetches_once(store_env):
    workers = 8
    start = threading.Barrier(workers)
    calls_lock = threading.Lock()

    def slow_fetcher(pin: dict, workdir: Path) -> Path:
        with calls_lock:
            store_env.calls["n"] += 1
        # Delay fetch so every caller contends on the install lock.
        time.sleep(0.1)
        out = workdir / store_env.wheel.name
        out.write_bytes(store_env.wheel.read_bytes())
        return out

    def ensure() -> Path:
        start.wait()
        return _ensure_engines(slow_fetcher)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        roots = list(pool.map(lambda _i: ensure(), range(workers)))

    assert roots == [store_env.store_dir] * workers
    assert store_env.calls["n"] == 1
    assert sha256_tree(store_env.store_dir) == store_env.store_sha


def test_ensure_waits_for_long_held_install_lock(store_env, capsys):
    entered = threading.Event()

    def hold_lock():
        with engines._engine_install_lock(store_env.store_dir):
            entered.set()
            time.sleep(12.25)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    # Generous window: a cold CI runner can take seconds to open the lock file.
    assert entered.wait(timeout=30), "holder did not acquire the install lock"

    started = time.monotonic()
    root = _ensure_engines(store_env.fetcher)
    elapsed = time.monotonic() - started

    holder.join(timeout=10)
    assert not holder.is_alive(), "holder did not release the install lock"
    assert elapsed >= 12, elapsed
    assert root == store_env.store_dir
    assert store_env.calls["n"] == 1
    if os.name == "nt":
        assert "waiting for engines lock (another tackbox is fetching)" in (
            capsys.readouterr().err
        )


# -- ensure: the opengrep payload ------------------------------------------


def test_opengrep_payload_installs_into_its_own_store(store_env):
    """tackbox-opengrep resolves through the same mechanism as tackbox-engines
    but by its own pin, wheel prefix, and store directory."""
    seen: list[dict] = []

    def og_fetcher(pin: dict, workdir: Path) -> Path:
        seen.append(pin)
        out = workdir / store_env.og_wheel.name
        out.write_bytes(store_env.og_wheel.read_bytes())
        return out

    root = engines.ensure_payload(engines.OPENGREP, fetcher=og_fetcher)
    assert root == store_env.og_store_dir
    assert seen == [store_env.og_pin]
    for rel, content in _OPENGREP_PAYLOAD.items():
        assert (root / rel).read_bytes() == content
    assert sha256_tree(root) == store_env.og_pin["store_sha256"]
    assert not store_env.store_dir.exists(), "opengrep must not touch the engines store"
    assert engines.payload_root(engines.OPENGREP) == store_env.og_store_dir


def test_opengrep_refuses_an_engines_wheel(store_env):
    """A wheel without the `tackbox_opengrep/` subtree is not an opengrep
    payload, whatever its name; nothing is committed to the store."""
    with pytest.raises(engines.EnginesStoreError, match="tackbox_opengrep/"):
        engines.ensure_payload(engines.OPENGREP, fetcher=store_env.fetcher)
    assert not store_env.og_store_dir.exists()


def test_ensure_payloads_materializes_both_stores(store_env, monkeypatch):
    fetchers = {
        "tackbox-engines": store_env.wheel,
        "tackbox-opengrep": store_env.og_wheel,
    }

    def by_project(pin: dict, workdir: Path) -> Path:
        src = fetchers[pin["project"]]
        out = workdir / src.name
        out.write_bytes(src.read_bytes())
        return out

    monkeypatch.setattr(engines, "_download_wheel", by_project)
    engines.ensure_payloads()
    assert store_env.store_dir.is_dir()
    assert store_env.og_store_dir.is_dir()


def test_hermetic_env_puts_both_payload_bins_on_path(store_env, monkeypatch, tmp_path):
    monkeypatch.setenv("TACKBOX_ENGINES_DIR", str(tmp_path / "eng"))
    monkeypatch.setenv("TACKBOX_OPENGREP_DIR", str(tmp_path / "og"))
    env = engines.hermetic_env({"PATH": "/usr/bin"})
    assert env["PATH"].split(os.pathsep) == [
        str(tmp_path / "eng" / "bin"), str(tmp_path / "og" / "bin"), "/usr/bin",
    ]
    assert env["NODE_PATH"] == str(tmp_path / "eng" / "vendor" / "node_modules")


# -- ensure: override ------------------------------------------------------


def test_override_dir_short_circuits_fetch(store_env, monkeypatch, tmp_path):
    override = tmp_path / "unpacked-dist-fat"
    override.mkdir()
    monkeypatch.setenv("TACKBOX_ENGINES_DIR", str(override))

    def explode(pin, workdir):
        raise AssertionError("override must skip the fetcher entirely")

    root = _ensure_engines(explode)
    assert root == override
    assert engines.payload_root(engines.ENGINES) == override


def test_opengrep_override_dir_short_circuits_fetch(store_env, monkeypatch, tmp_path):
    override = tmp_path / "unpacked-dist-opengrep"
    override.mkdir()
    monkeypatch.setenv("TACKBOX_OPENGREP_DIR", str(override))

    def explode(pin, workdir):
        raise AssertionError("override must skip the fetcher entirely")

    assert engines.ensure_payload(engines.OPENGREP, fetcher=explode) == override
    assert engines.payload_root(engines.OPENGREP) == override
    # One payload's override never redirects the other.
    assert engines.payload_root(engines.ENGINES) == store_env.store_dir


# -- ensure: adversarial (attack the guarantee) ---------------------------


def test_repacked_container_with_identical_tree_installs(store_env):
    # The published wheel is a different zip (member order, timestamps,
    # comment) around the same payload: rebuilds are not zip-reproducible and
    # PyPI keeps the first upload. Only the payload tree is the pin; the
    # container bytes must not matter.
    def repack_fetcher(pin: dict, workdir: Path) -> Path:
        out = workdir / pin["wheel"]
        with zipfile.ZipFile(out, "w") as zf:
            zf.comment = b"repacked container, same tree"
            for rel, content in sorted(store_env.payload.items(), reverse=True):
                info = zipfile.ZipInfo(f"tackbox_engines/{rel}", date_time=(2020, 1, 2, 3, 4, 6))
                info.external_attr = (0o755 if rel in _EXEC else 0o644) << 16
                zf.writestr(info, content)
        return out

    root = _ensure_engines(repack_fetcher)
    assert root == store_env.store_dir
    assert sha256_tree(root) == store_env.store_sha


def test_corrupt_tree_after_unpack_hard_errors(store_env):
    # The tree pin is the integrity barrier: a wheel whose payload does not
    # match store_sha256 must be refused, and nothing committed to the store.
    store_env.pin["store_sha256"] = "bb" * 32

    with pytest.raises(engines.EnginesStoreError) as ei:
        _ensure_engines(store_env.fetcher)
    assert "bb" * 32 in str(ei.value) and "tackbox-engines" in str(ei.value)
    assert not store_env.store_dir.exists()
    # No half-written temp siblings survive a failed install. The advisory lock
    # file persists deliberately: unlinking it could split current waiters and
    # later callers across different inodes, defeating mutual exclusion.
    base = store_env.store_dir.parent
    entries = list(base.iterdir())
    assert [p.name for p in entries] == [f".{store_env.version}.install.lock"]


def test_platform_drift_hard_errors_with_both_values_before_fetch(store_env):
    # The Rosetta / wrong-wheel case: engines.json pins payload wheels for a
    # different platform than the one we are running on. ensure must refuse
    # loudly, naming both platforms, and must never reach the fetcher.
    detected = _detected_platform()
    foreign = "linux-x86_64" if detected != "linux-x86_64" else "macos-aarch64"
    store_env.data["platform"] = foreign

    def explode(pin, workdir):
        raise AssertionError("platform drift must be caught before any fetch")

    for payload in engines.PAYLOADS:
        with pytest.raises(engines.EnginesStoreError) as ei:
            engines.ensure_payload(payload, fetcher=explode)
        msg = str(ei.value)
        assert foreign in msg and detected in msg
    assert not store_env.store_dir.exists()
    assert not store_env.og_store_dir.exists()


def test_fetch_failure_propagates_loudly_and_leaves_no_store(store_env):
    def failing(pin, workdir):
        raise engines.EnginesStoreError(
            "cannot download https://pypi.org/.../tackbox_engines.whl: timed out"
        )

    with pytest.raises(engines.EnginesStoreError) as ei:
        _ensure_engines(failing)
    assert "https://pypi.org" in str(ei.value)
    assert not store_env.store_dir.exists()

    # Advisory locks are kernel-owned: a failed/crashed first attempt must not
    # strand later hooks behind stale lock state.
    assert _ensure_engines(store_env.fetcher) == store_env.store_dir
    assert store_env.calls["n"] == 1


# -- ensure: version retention ---------------------------------------------


def test_install_preserves_other_versions_and_inflight_state(store_env):
    base = store_env.store_dir.parent
    base.mkdir(parents=True)
    previous = base / "0.1.0"
    previous.mkdir()
    (previous / "marker").write_text("old")
    inflight = base / ".ensure-someone-else"  # dot-prefixed: a concurrent fetch's tmp
    inflight.mkdir()
    (inflight / "partial").write_text("mid-unpack")

    _ensure_engines(store_env.fetcher)

    assert store_env.store_dir.is_dir()
    assert previous.is_dir(), "an older client must not be forced to re-download its engines"
    assert inflight.is_dir(), "one installer must not disturb another version's state"


# -- payload_root ----------------------------------------------------------


def test_payload_root_resolves_versioned_store(store_env):
    assert engines.payload_root(engines.ENGINES) == store_env.store_dir


def test_payload_root_prefers_override(store_env, monkeypatch, tmp_path):
    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setenv("TACKBOX_ENGINES_DIR", str(override))
    assert engines.payload_root(engines.ENGINES) == override


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
    got = engines._download_wheel(store_env.pin, workdir)
    assert got.read_bytes() == wheel_bytes
    assert seen[0] == "https://pypi.org/pypi/tackbox-engines/0.9.0/json"
    assert download_url in seen


def test_default_fetcher_queries_the_pinned_project(store_env, monkeypatch):
    seen: list[str] = []
    index = {"urls": [{"filename": store_env.og_wheel.name, "url": "https://x/og"}]}

    def fake_urlopen(url, timeout=None):
        seen.append(url)
        if url.endswith("/json"):
            return io.BytesIO(json.dumps(index).encode())
        return io.BytesIO(store_env.og_wheel.read_bytes())

    monkeypatch.setattr(engines, "urlopen", fake_urlopen)
    workdir = store_env.tmp / "dl-og"
    workdir.mkdir()
    engines._download_wheel(store_env.og_pin, workdir)
    assert seen[0] == "https://pypi.org/pypi/tackbox-opengrep/0.3.0/json"


def test_default_fetcher_missing_wheel_in_index_hard_errors(store_env, monkeypatch):
    index = {"urls": [{"filename": "some-other-file.whl", "url": "https://x/y"}]}
    monkeypatch.setattr(
        engines, "urlopen", lambda url, timeout=None: io.BytesIO(json.dumps(index).encode())
    )
    workdir = store_env.tmp / "dl2"
    workdir.mkdir()
    with pytest.raises(engines.EnginesStoreError) as ei:
        engines._download_wheel(store_env.pin, workdir)
    assert store_env.wheel.name in str(ei.value)


def test_default_fetcher_rejects_download_mismatching_index_digest(store_env, monkeypatch):
    # Transport barrier: the bytes received differ from what the index itself
    # claims to serve (truncation, corrupting proxy) - loud error.
    download_url = "https://files.pythonhosted.org/packages/ab/cd/" + store_env.wheel.name
    index = {
        "urls": [
            {
                "filename": store_env.wheel.name,
                "url": download_url,
                "digests": {"sha256": "cc" * 32},
            }
        ]
    }

    def fake_urlopen(url, timeout=None):
        if url.endswith("/json"):
            return io.BytesIO(json.dumps(index).encode())
        return io.BytesIO(store_env.wheel.read_bytes())

    monkeypatch.setattr(engines, "urlopen", fake_urlopen)
    monkeypatch.setattr(engines, "_DOWNLOAD_BACKOFF_SECONDS", 0)
    workdir = store_env.tmp / "dl3"
    workdir.mkdir()
    with pytest.raises(engines.EnginesStoreError) as ei:
        engines._download_wheel(store_env.pin, workdir)
    assert "cc" * 32 in str(ei.value)


def _sized_index(store_env, download_url: str, size: int) -> dict:
    return {
        "urls": [
            {"filename": store_env.wheel.name, "url": download_url, "size": size}
        ]
    }


def test_default_fetcher_retries_a_truncated_download(store_env, monkeypatch):
    # A connection dropped mid-body arrives as a short read, never an exception,
    # so only the size check separates it from a substituted file.
    wheel_bytes = store_env.wheel.read_bytes()
    download_url = "https://files.pythonhosted.org/packages/ab/cd/" + store_env.wheel.name
    index = _sized_index(store_env, download_url, len(wheel_bytes))
    bodies = [wheel_bytes[: len(wheel_bytes) // 2], wheel_bytes]

    def fake_urlopen(url, timeout=None):
        if url.endswith("/json"):
            return io.BytesIO(json.dumps(index).encode())
        return io.BytesIO(bodies.pop(0))

    monkeypatch.setattr(engines, "urlopen", fake_urlopen)
    monkeypatch.setattr(engines, "_DOWNLOAD_BACKOFF_SECONDS", 0)
    workdir = store_env.tmp / "dl4"
    workdir.mkdir()
    got = engines._download_wheel(store_env.pin, workdir)
    assert got.read_bytes() == wheel_bytes
    assert bodies == []


def test_default_fetcher_gives_up_after_the_attempt_budget(store_env, monkeypatch):
    wheel_bytes = store_env.wheel.read_bytes()
    download_url = "https://files.pythonhosted.org/packages/ab/cd/" + store_env.wheel.name
    index = _sized_index(store_env, download_url, len(wheel_bytes))
    attempts: list[str] = []

    def fake_urlopen(url, timeout=None):
        if url.endswith("/json"):
            return io.BytesIO(json.dumps(index).encode())
        attempts.append(url)
        return io.BytesIO(wheel_bytes[:10])

    monkeypatch.setattr(engines, "urlopen", fake_urlopen)
    monkeypatch.setattr(engines, "_DOWNLOAD_BACKOFF_SECONDS", 0)
    workdir = store_env.tmp / "dl5"
    workdir.mkdir()
    with pytest.raises(engines.EnginesStoreError) as ei:
        engines._download_wheel(store_env.pin, workdir)
    assert len(attempts) == engines._DOWNLOAD_ATTEMPTS
    assert "truncated download" in str(ei.value)


# -- unpack: zip-slip containment (A4) ------------------------------------

# The manual `zf.open` + `target.open("wb")` unpack bypasses ZipFile.extract's
# `..` sanitizing, and the store_sha256 tree pin is verified only after
# extraction over the files that landed inside dest - a member written outside
# dest is invisible to it. So an escaping member must be refused BEFORE any
# write, not after. These build wheels whose member names are written verbatim
# (an escaping name cannot be produced by the prefix-forcing _build_payload_wheel).


def _build_wheel_with_members(
    dest_dir: Path, name: str, members: dict[str, bytes]
) -> Path:
    wheel = dest_dir / name
    with zipfile.ZipFile(wheel, "w") as zf:
        for member, content in members.items():
            zf.writestr(member, content)
    return wheel


def test_unpack_rejects_dotdot_member_and_writes_nothing_outside_dest(tmp_path):
    # `tackbox_engines/../escaped.txt` lexically passes the prefix filter but
    # resolves one level above dest. It must be refused, and the pre-scan must
    # fail before writing even the legit member.
    outer = tmp_path / "outer"
    outer.mkdir()
    dest = outer / "store"
    wheel = _build_wheel_with_members(
        tmp_path,
        "malicious-dotdot.whl",
        {
            "tackbox_engines/bin/node": b"#!/bin/sh\n",
            "tackbox_engines/../escaped.txt": b"pwned\n",
        },
    )
    with pytest.raises(engines.EnginesStoreError) as ei:
        engines._unpack_payload(wheel, dest, "tackbox_engines")
    assert "escape" in str(ei.value).lower()
    # The escaping member resolves to outer/escaped.txt: it must not exist, and
    # nothing may have been planted alongside dest.
    assert not (outer / "escaped.txt").exists()
    assert {p.name for p in outer.iterdir()} <= {"store"}


def test_unpack_rejects_absolute_member_and_writes_nothing_outside_dest(tmp_path):
    # `tackbox_engines/<absolute>` strips to an absolute path; joined to dest it
    # replaces dest entirely (pathlib), writing at the absolute location. Must be
    # refused before that write.
    outer = tmp_path / "outer"
    outer.mkdir()
    dest = outer / "store"
    abs_target = tmp_path / "abs_escaped.txt"  # absolute path outside dest
    wheel = _build_wheel_with_members(
        tmp_path,
        "malicious-abs.whl",
        {
            "tackbox_engines/bin/node": b"#!/bin/sh\n",
            f"tackbox_engines/{abs_target}": b"pwned\n",
        },
    )
    with pytest.raises(engines.EnginesStoreError) as ei:
        engines._unpack_payload(wheel, dest, "tackbox_engines")
    assert "absolute" in str(ei.value).lower()
    assert not abs_target.exists()


def test_unpack_valid_payload_lands_entirely_inside_dest(tmp_path):
    # The containment guard must not disturb a normal all-inside payload.
    dest = tmp_path / "store"
    payload = {
        "tackbox_engines/bin/node": b"#!/bin/sh\necho node\n",
        "tackbox_engines/vendor/node_modules/eslint/index.js": b"module.exports={}\n",
    }
    wheel = _build_wheel_with_members(tmp_path, "clean.whl", payload)
    engines._unpack_payload(wheel, dest, "tackbox_engines")
    assert (dest / "bin" / "node").read_bytes() == b"#!/bin/sh\necho node\n"
    assert (
        dest / "vendor" / "node_modules" / "eslint" / "index.js"
    ).read_bytes() == b"module.exports={}\n"

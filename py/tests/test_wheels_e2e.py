"""Session-scoped e2e: build thin/fat wheels, install the thin wheel alone
into a fresh venv, and drive tackbox against the shared fixture repo with the
engine payload supplied via TACKBOX_ENGINES_DIR.

F6: the thin wheel no longer depends on tackbox-engines; the engine binaries
come from the machine store, fetched from PyPI at runtime. A CI/e2e run can't
fetch an unpublished version, so it points TACKBOX_ENGINES_DIR at the unpacked
fat wheel - the store's override path - which also keeps the runtime offline.

Fixture materialization lives in `scripts/materialize_fixture.py` so the same
seeded violations drive both this pytest session and the wheels CI matrix.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tackbox import engines
from tackbox.cache import sha256_tree

REPO = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO / "scripts" / "build_wheels.py"
MATERIALIZE_SCRIPT = REPO / "scripts" / "materialize_fixture.py"


def _needs(cmd: str) -> None:
    if not shutil.which(cmd):
        pytest.fail(f"`{cmd}` toolchain not found on PATH; install it, do not skip")


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, **kw)


@pytest.fixture(scope="session")
def wheels(tmp_path_factory) -> dict:
    _needs("npm")
    _needs("go")
    _needs("uv")
    _needs("mvn")
    outdir = tmp_path_factory.mktemp("dist-wheels")
    env = {**os.environ, "TACKBOX_VERSION": "0.0.0", "TACKBOX_ENGINES_VERSION": "0.0.0"}
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--outdir", str(outdir), "--version", "0.0.0"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"build_wheels.py exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    payload = json.loads(result.stdout)
    return {"outdir": outdir, "fat": Path(payload["fat"]), "thin": Path(payload["thin"])}


@pytest.fixture(scope="session")
def hermetic_venv(tmp_path_factory, wheels) -> Path:
    """A fresh venv with ONLY the thin wheel installed.

    Installing thin alone (no fat) is itself the F6 contract check: if thin
    still pinned tackbox-engines in its metadata, uv would fail to resolve.
    """
    v = tmp_path_factory.mktemp("hermetic-venv")
    # `uv venv` because `python -m venv` off a uv-managed interpreter leaves
    # libpython unresolvable via dyld @rpath on macOS.
    env = {k: val for k, val in os.environ.items() if k != "VIRTUAL_ENV"}
    subprocess.run(
        ["uv", "venv", str(v)],
        check=True, capture_output=True, text=True, env=env,
    )
    result = subprocess.run(
        ["uv", "pip", "install", "--python", str(v / "bin" / "python"), str(wheels["thin"])],
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"installing the thin wheel alone failed with exit {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return v


@pytest.fixture(scope="session")
def engines_payload(tmp_path_factory, wheels) -> Path:
    """The fat wheel's payload unpacked into a store-override directory, using
    the same unpack the runtime uses (exec bits restored)."""
    d = tmp_path_factory.mktemp("engines-payload") / "tackbox_engines"
    engines._unpack_tackbox_engines(wheels["fat"], d)
    return d


@pytest.fixture(scope="session")
def fixture_repo(tmp_path_factory) -> Path:
    parent = tmp_path_factory.mktemp("hermetic-fixture-parent")
    root = parent / "repo"
    subprocess.run(
        [sys.executable, str(MATERIALIZE_SCRIPT), str(root)],
        check=True, capture_output=True, text=True,
    )
    return root


def _tackbox(venv: Path) -> Path:
    return venv / "bin" / "tackbox"


def _hermetic_env(engines_payload: Path) -> dict:
    return {**os.environ, "TACKBOX_ENGINES_DIR": str(engines_payload)}


def test_thin_and_fat_wheels_built(wheels):
    assert wheels["fat"].is_file()
    assert wheels["thin"].is_file()
    assert wheels["fat"].name.endswith(".whl")
    assert wheels["thin"].name.endswith(".whl")


def test_build_restores_source_tree(wheels):
    """Leftover fat artifacts in py/tackbox would ride into every dev
    `uv run --directory py` rebuild - gigabytes of uv cache over a day."""
    pkg = REPO / "py" / "tackbox"
    leftover_bins = list((pkg / "bin").glob("*")) if (pkg / "bin").exists() else []
    assert leftover_bins == [], f"bin not restored: {leftover_bins}"
    assert not (pkg / "third_party").exists(), "third_party not restored"
    assert not (pkg / "rules").exists(), "materialized rules not restored"
    assert not (pkg / "engines.json").exists(), "engines.json not restored"


def test_thin_wheel_carries_javalint_jar(wheels):
    """F8a: the thin wheel ships the platform-independent javalint.jar and pins
    it in engines.json, so doctor checksums it and F8d can dispatch it. A
    dropped jar must fail the build here, not the consumer."""
    with zipfile.ZipFile(wheels["thin"]) as zf:
        names = set(zf.namelist())
        ej = json.loads(zf.read("tackbox/engines.json"))
    assert "tackbox/bin/javalint.jar" in names, sorted(
        n for n in names if n.startswith("tackbox/bin/")
    )
    entry = next((e for e in ej["engines"] if e.get("id") == "javalint"), None)
    assert entry is not None, "engines.json is missing the javalint entry"
    assert entry["path"] == "tackbox/bin/javalint.jar"
    assert entry.get("sha256"), "javalint entry must carry a sha256 for doctor"


def test_thin_wheel_does_not_depend_on_fat(wheels):
    """F6: thin must not carry a Requires-Dist on tackbox-engines - the engine
    payload is a machine-store fetch, not a pip dependency."""
    with zipfile.ZipFile(wheels["thin"]) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        metadata = zf.read(meta_name).decode("utf-8")
    requires = [ln for ln in metadata.splitlines() if ln.startswith("Requires-Dist:")]
    assert not any("tackbox-engines" in r or "tackbox_engines" in r for r in requires), (
        f"thin wheel still pins fat: {requires}"
    )
    # flake8 stays a real dependency (hosts the pyrules plugin).
    assert any("flake8" in r for r in requires), f"thin must still require flake8: {requires}"


def test_engines_json_carries_store_pins(engines_payload, wheels):
    """The thin wheel's engines.json must pin the fat wheel (name + this
    platform) and the unpacked tree, and the pin must match the real payload."""
    with zipfile.ZipFile(wheels["thin"]) as zf:
        ej = json.loads(zf.read("tackbox/engines.json"))
    assert ej["engines_version"] == "0.0.0"
    fw = ej["fat_wheel"]
    assert fw["wheel"] == wheels["fat"].name
    assert fw["platform"] == engines.detect_platform_key()
    assert "sha256" not in fw
    assert ej["store_sha256"] == sha256_tree(engines_payload)


def test_hermetic_doctor_exits_zero(hermetic_venv, fixture_repo, engines_payload):
    result = _run(
        [str(_tackbox(hermetic_venv)), "doctor"],
        cwd=fixture_repo, env=_hermetic_env(engines_payload),
    )
    assert result.returncode == 0, (
        f"doctor failed: {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "doctor: 7 checks, 0 failed" in result.stdout
    assert "ok platform:" in result.stdout
    assert "ok engines-store:" in result.stdout
    assert "ok payload-checksums:" in result.stdout
    assert "ok binaries-start:" in result.stdout
    assert "ok git-in-path:" in result.stdout
    assert "ok go-toolchain:" in result.stdout
    assert "ok java-toolchain:" in result.stdout


def test_hermetic_lint_finds_all_engine_violations(hermetic_venv, fixture_repo, engines_payload):
    result = _run(
        [str(_tackbox(hermetic_venv)), "lint", ".", "--no-cache"],
        cwd=fixture_repo, env=_hermetic_env(engines_payload),
    )
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "== erclint ==" in result.stdout
    assert "== erclint-opengrep ==" in result.stdout
    assert "== javalint ==" in result.stdout
    assert "== tackbox-eslint ==" in result.stdout
    assert "== tackbox-mdlint ==" in result.stdout
    # erclint ERC001 on pkga/violate.go
    assert "ERC001" in result.stdout
    assert "pkga" in result.stdout
    # javalint JV001 (swallowed catch) on Handler.java - the hermetic `java -jar`
    # path end to end, from the jar packed into the thin wheel.
    assert "JV001" in result.stdout
    assert "Handler.java" in result.stdout
    # same JV001, one directory deep: the repo-relative key must use "/" even
    # on a Windows runner (javasub/Deep.java, not javasub\Deep.java).
    assert "javasub/Deep.java" in result.stdout
    assert "javasub\\Deep.java" not in result.stdout
    # eslint no-swallow-catch on swallow.js
    assert "no-swallow-catch" in result.stdout
    # markdownlint MD-ASCII on notes.md
    assert "MD-ASCII" in result.stdout or "no-non-ascii" in result.stdout


def test_doctor_fails_on_patched_vendored_transitive_dep(hermetic_venv, fixture_repo, engines_payload):
    """A byte flipped anywhere in the store payload must turn doctor red.

    Targets a transitive dep specifically: those are covered only by the
    vendor-tree entry and the whole-tree store_sha256 - exactly the holes these
    two checks pin shut.
    """
    tackbox = _tackbox(hermetic_venv)
    env = _hermetic_env(engines_payload)
    engines_json = json.loads((_site_packages(hermetic_venv) / "tackbox" / "engines.json").read_text())
    top_level = {
        e["path"].split("node_modules/")[-1].split("/")[0]
        for e in engines_json["engines"]
        if e.get("kind") == "npm"
    }
    vendor = engines_payload / "vendor" / "node_modules"
    victim = next(
        p for p in sorted(vendor.rglob("*.js"))
        if p.is_file() and p.relative_to(vendor).parts[0] not in top_level
    )
    original = victim.read_bytes()
    try:
        victim.write_bytes(original + b"\n// locally patched\n")
        result = _run([str(tackbox), "doctor"], cwd=fixture_repo, env=env)
        assert result.returncode == 1, (
            f"patched vendored dep must fail doctor\nSTDOUT:\n{result.stdout}"
        )
        assert "fail engines-store:" in result.stdout
        assert "fail payload-checksums:" in result.stdout
    finally:
        victim.write_bytes(original)
    result = _run([str(tackbox), "doctor"], cwd=fixture_repo, env=env)
    assert result.returncode == 0, "doctor must recover after restore"


def test_hermetic_banner_carries_engines_sha_and_versions(hermetic_venv, fixture_repo, engines_payload):
    result = _run(
        [str(_tackbox(hermetic_venv)), "lint", ".", "--no-cache"],
        cwd=fixture_repo, env=_hermetic_env(engines_payload),
    )
    banner = None
    for line in result.stderr.splitlines():
        if line.startswith("tackbox "):
            banner = line
            break
    assert banner is not None, f"no banner on stderr:\n{result.stderr}"
    assert "engines=sha256:" in banner
    assert "opengrep=1.25.0" in banner
    assert "node=22.18.0" in banner
    assert "eslint=" in banner
    assert "markdownlint=" in banner


def _site_packages(venv: Path) -> Path:
    return next((venv / "lib").glob("python*/site-packages"))

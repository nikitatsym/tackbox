"""Session-scoped e2e: build thin/fat wheels, install into a fresh venv,
run tackbox lint/doctor on an inline fixture repo."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO / "scripts" / "build_wheels.py"

_FIXTURE_GO_MOD = """module e2e-fixture

go 1.21
"""

_FIXTURE_GO_ERC001 = """package pkga

import "errors"

func Do() {
\terr := errors.New("bad")
\tif err != nil {
\t\treturn
\t}
}
"""

_FIXTURE_GO_ERC006 = """package pkgb

import "context"

func sentryErr(ctx context.Context, msg string, err error, tags map[string]string, key string) {}

func Report(ctx context.Context, msg string, err error, tags map[string]string) {
\tsentryErr(ctx, msg, err, tags, "user.token")
}
"""

_FIXTURE_JS_SWALLOW = """try {
  doSomething()
} catch (e) {
}
"""

_FIXTURE_MD = "# Title — dash goes here\n"


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
    v = tmp_path_factory.mktemp("hermetic-venv")
    # `uv venv` because `python -m venv` off a uv-managed interpreter
    # leaves libpython unresolvable via dyld @rpath on macOS.
    env = {k: val for k, val in os.environ.items() if k != "VIRTUAL_ENV"}
    subprocess.run(
        ["uv", "venv", str(v)],
        check=True, capture_output=True, text=True, env=env,
    )
    result = subprocess.run(
        ["uv", "pip", "install", "--python", str(v / "bin" / "python"),
         str(wheels["fat"]), str(wheels["thin"])],
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"uv pip install failed with exit {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return v


@pytest.fixture(scope="session")
def fixture_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("hermetic-fixture-repo")
    (root / "go.mod").write_text(_FIXTURE_GO_MOD)
    (root / "pkga").mkdir()
    (root / "pkga" / "violate.go").write_text(_FIXTURE_GO_ERC001)
    (root / "pkgb").mkdir()
    (root / "pkgb" / "secret.go").write_text(_FIXTURE_GO_ERC006)
    (root / "swallow.js").write_text(_FIXTURE_JS_SWALLOW)
    (root / "notes.md").write_text(_FIXTURE_MD)
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=root, check=True, capture_output=True,
    )
    return root


def test_thin_and_fat_wheels_built(wheels):
    assert wheels["fat"].is_file()
    assert wheels["thin"].is_file()
    assert wheels["fat"].name.endswith(".whl")
    assert wheels["thin"].name.endswith(".whl")


def test_hermetic_doctor_exits_zero(hermetic_venv, fixture_repo):
    tackbox = hermetic_venv / "bin" / "tackbox"
    result = _run([str(tackbox), "doctor"], cwd=fixture_repo)
    assert result.returncode == 0, (
        f"doctor failed: {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "doctor: 5 checks, 0 failed" in result.stdout
    assert "ok platform:" in result.stdout
    assert "ok payload-checksums:" in result.stdout
    assert "ok binaries-start:" in result.stdout
    assert "ok git-in-path:" in result.stdout
    assert "ok go-toolchain:" in result.stdout


def test_hermetic_lint_finds_all_engine_violations(hermetic_venv, fixture_repo):
    tackbox = hermetic_venv / "bin" / "tackbox"
    result = _run(
        [str(tackbox), "lint", ".", "--no-cache"],
        cwd=fixture_repo,
    )
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "== erclint ==" in result.stdout
    assert "== erclint-opengrep ==" in result.stdout
    assert "== tackbox-eslint ==" in result.stdout
    assert "== tackbox-mdlint ==" in result.stdout
    # erclint ERC001 on pkga/violate.go
    assert "ERC001" in result.stdout
    assert "pkga" in result.stdout
    # eslint no-swallow-catch on swallow.js
    assert "no-swallow-catch" in result.stdout
    # markdownlint MD-ASCII on notes.md
    assert "MD-ASCII" in result.stdout or "no-non-ascii" in result.stdout


def test_doctor_fails_on_patched_vendored_transitive_dep(hermetic_venv, fixture_repo):
    """A byte flipped anywhere in the vendored tree must turn doctor red.

    Targets a transitive dep specifically: those are covered only by the
    vendor-tree entry, which is exactly the hole this test pins shut.
    """
    tackbox = hermetic_venv / "bin" / "tackbox"
    site = next((hermetic_venv / "lib").glob("python*/site-packages"))
    engines_json = json.loads((site / "tackbox" / "engines.json").read_text())
    top_level = {
        e["path"].split("node_modules/")[-1].split("/")[0]
        for e in engines_json["engines"]
        if e.get("kind") == "npm"
    }
    vendor = site / "tackbox_engines" / "vendor" / "node_modules"
    victim = next(
        p for p in sorted(vendor.rglob("*.js"))
        if p.is_file() and p.relative_to(vendor).parts[0] not in top_level
    )
    original = victim.read_bytes()
    try:
        victim.write_bytes(original + b"\n// locally patched\n")
        result = _run([str(tackbox), "doctor"], cwd=fixture_repo)
        assert result.returncode == 1, (
            f"patched vendored dep must fail doctor\nSTDOUT:\n{result.stdout}"
        )
        assert "fail payload-checksums:" in result.stdout
    finally:
        victim.write_bytes(original)
    result = _run([str(tackbox), "doctor"], cwd=fixture_repo)
    assert result.returncode == 0, "doctor must recover after restore"


def test_hermetic_banner_carries_engines_sha_and_versions(hermetic_venv, fixture_repo):
    tackbox = hermetic_venv / "bin" / "tackbox"
    result = _run(
        [str(tackbox), "lint", ".", "--no-cache"],
        cwd=fixture_repo,
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

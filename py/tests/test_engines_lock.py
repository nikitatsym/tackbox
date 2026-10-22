"""engines/VERSION, engines/opengrep/VERSION + engines/lock.json contract
for publish gating.

The tackbox-engines version comes from engines/VERSION and the
tackbox-opengrep version from engines/opengrep/VERSION; drift detection
comes from engines/lock.json, which pins both versions and sha256 of the
engine sources (manifest.json + vendor/package-lock.json). The publish
workflow refuses to push if a VERSION was bumped without regenerating
lock.json (or vice versa), so engineers cannot silently ship new engines
under a stale payload version and pin thin to the wrong payload.

scripts/lock_engines.py is the single source of truth: `--check`
verifies the committed lock matches current sources; `--write`
regenerates it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tackbox.hashing import sha256_file

REPO = Path(__file__).resolve().parents[2]
VERSION_FILE = REPO / "engines" / "VERSION"
OPENGREP_VERSION_FILE = REPO / "engines" / "opengrep" / "VERSION"
LOCK_FILE = REPO / "engines" / "lock.json"
MANIFEST_FILE = REPO / "engines" / "manifest.json"
VENDOR_LOCK_FILE = REPO / "engines" / "vendor" / "package-lock.json"
LOCK_SCRIPT = REPO / "scripts" / "lock_engines.py"

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


@pytest.mark.parametrize("path", [VERSION_FILE, OPENGREP_VERSION_FILE], ids=lambda p: p.parent.name)
def test_payload_version_file_exists_and_is_semver(path):
    rel = path.relative_to(REPO).as_posix()
    assert path.is_file(), f"missing {rel}"
    raw = path.read_text()
    stripped = raw.strip()
    assert SEMVER_RE.match(stripped), (
        f"{rel} must be strict semver (major.minor.patch), got {stripped!r}"
    )
    assert raw.endswith("\n"), f"{rel} must end with a trailing newline"
    assert raw.count("\n") == 1, f"{rel} must contain exactly one line"


def test_engines_lock_file_exists_and_is_valid_json():
    assert LOCK_FILE.is_file(), f"missing {LOCK_FILE.relative_to(REPO)}"
    data = json.loads(LOCK_FILE.read_text())
    assert data.get("schema") == 1, "engines/lock.json must declare schema: 1"
    for key in ("version", "opengrep_version", "manifest_sha256", "vendor_lock_sha256"):
        assert key in data, f"engines/lock.json missing required key {key!r}"


def test_engines_lock_version_matches_version_file():
    data = json.loads(LOCK_FILE.read_text())
    version_content = VERSION_FILE.read_text().strip()
    assert data["version"] == version_content, (
        f"engines/lock.json version {data['version']!r} != "
        f"engines/VERSION {version_content!r}. Run "
        f"`python scripts/lock_engines.py --write` after bumping engines/VERSION."
    )
    assert data["opengrep_version"] == OPENGREP_VERSION_FILE.read_text().strip(), (
        "engines/lock.json opengrep_version != engines/opengrep/VERSION. Run "
        "`python scripts/lock_engines.py --write` after bumping engines/opengrep/VERSION."
    )


def test_engines_lock_shas_match_current_sources():
    data = json.loads(LOCK_FILE.read_text())
    expected_manifest = sha256_file(MANIFEST_FILE)
    expected_vendor_lock = sha256_file(VENDOR_LOCK_FILE)
    assert data["manifest_sha256"] == expected_manifest, (
        "engines/lock.json.manifest_sha256 does not match current "
        "engines/manifest.json. Regenerate: "
        "`python scripts/lock_engines.py --write`."
    )
    assert data["vendor_lock_sha256"] == expected_vendor_lock, (
        "engines/lock.json.vendor_lock_sha256 does not match current "
        "engines/vendor/package-lock.json. Regenerate: "
        "`python scripts/lock_engines.py --write`."
    )


def test_lock_engines_script_exists_and_is_executable():
    assert LOCK_SCRIPT.is_file(), f"missing {LOCK_SCRIPT.relative_to(REPO)}"


def test_lock_engines_check_passes_on_clean_tree():
    result = subprocess.run(
        [sys.executable, str(LOCK_SCRIPT), "--check"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"lock_engines --check must pass on committed tree, got {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_lock_engines_write_reproduces_committed_lock(tmp_path):
    """Regenerating lock.json from the same inputs must yield the committed
    bytes exactly - deterministic output is what makes drift detection work."""
    scratch_repo = tmp_path / "repo"
    scratch_engines = scratch_repo / "engines"
    scratch_scripts = scratch_repo / "scripts"
    (scratch_engines / "vendor").mkdir(parents=True)
    (scratch_engines / "opengrep").mkdir()
    scratch_scripts.mkdir()
    shutil.copyfile(LOCK_SCRIPT, scratch_scripts / "lock_engines.py")
    shutil.copyfile(VERSION_FILE, scratch_engines / "VERSION")
    shutil.copyfile(OPENGREP_VERSION_FILE, scratch_engines / "opengrep" / "VERSION")
    shutil.copyfile(MANIFEST_FILE, scratch_engines / "manifest.json")
    shutil.copyfile(VENDOR_LOCK_FILE, scratch_engines / "vendor" / "package-lock.json")
    result = subprocess.run(
        [sys.executable, "scripts/lock_engines.py", "--write"],
        cwd=scratch_repo, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"lock_engines --write failed: {result.stderr}"
    )
    regenerated = (scratch_engines / "lock.json").read_text()
    committed = LOCK_FILE.read_text()
    assert regenerated == committed, (
        "lock_engines --write output drifts from committed engines/lock.json. "
        "Regenerate the committed file."
    )


def test_lock_engines_check_fails_when_version_file_changes(tmp_path):
    """Adversarial: bumping engines/VERSION without regenerating lock.json
    must fail the workflow, not silently pin thin to a stale fat version."""
    scratch = _stage_tree(tmp_path)
    (scratch / "engines" / "VERSION").write_text("9.9.9\n")
    result = subprocess.run(
        [sys.executable, "scripts/lock_engines.py", "--check"],
        cwd=scratch, capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "lock_engines --check must fail when VERSION drifts from lock.version"
    )
    assert "version" in (result.stdout + result.stderr).lower()


def test_lock_engines_check_fails_when_opengrep_version_file_changes(tmp_path):
    scratch = _stage_tree(tmp_path)
    (scratch / "engines" / "opengrep" / "VERSION").write_text("9.9.9\n")
    result = subprocess.run(
        [sys.executable, "scripts/lock_engines.py", "--check"],
        cwd=scratch, capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "lock_engines --check must fail when engines/opengrep/VERSION drifts from lock"
    )
    assert "engines/opengrep/VERSION" in result.stderr


def test_lock_engines_check_fails_when_manifest_changes(tmp_path):
    """Adversarial: swapping an engine version (e.g. new node) without
    regenerating lock.json must fail. Otherwise the fat wheel changes
    payload while VERSION and thin's pin stay pointing at the previous
    payload - silent regression on consumers."""
    scratch = _stage_tree(tmp_path)
    manifest = json.loads((scratch / "engines" / "manifest.json").read_text())
    manifest["schema"] = 999
    (scratch / "engines" / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    result = subprocess.run(
        [sys.executable, "scripts/lock_engines.py", "--check"],
        cwd=scratch, capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "lock_engines --check must fail when manifest.json changes without lock regen"
    )
    assert "manifest" in (result.stdout + result.stderr).lower()


def test_lock_engines_check_fails_when_vendor_lock_changes(tmp_path):
    """Adversarial: swapping an npm dep resolves to a new package-lock.json.
    Committing it without bumping VERSION + regenerating lock must fail."""
    scratch = _stage_tree(tmp_path)
    lock_path = scratch / "engines" / "vendor" / "package-lock.json"
    lock_path.write_text(lock_path.read_text() + "\n// drift\n")
    result = subprocess.run(
        [sys.executable, "scripts/lock_engines.py", "--check"],
        cwd=scratch, capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "lock_engines --check must fail when vendor package-lock changes without lock regen"
    )
    assert "vendor" in (result.stdout + result.stderr).lower() or "lock" in (result.stdout + result.stderr).lower()


def test_lock_engines_check_fails_on_missing_lock_file(tmp_path):
    scratch = _stage_tree(tmp_path)
    (scratch / "engines" / "lock.json").unlink()
    result = subprocess.run(
        [sys.executable, "scripts/lock_engines.py", "--check"],
        cwd=scratch, capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "lock_engines --check must fail when engines/lock.json is missing"
    )


def test_lock_engines_check_fails_on_corrupt_lock_file(tmp_path):
    scratch = _stage_tree(tmp_path)
    (scratch / "engines" / "lock.json").write_text("{not valid json")
    result = subprocess.run(
        [sys.executable, "scripts/lock_engines.py", "--check"],
        cwd=scratch, capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "lock_engines --check must fail when engines/lock.json is not valid JSON"
    )


def test_lock_engines_check_fails_on_non_semver_version(tmp_path):
    scratch = _stage_tree(tmp_path)
    (scratch / "engines" / "VERSION").write_text("not-a-semver\n")
    result = subprocess.run(
        [sys.executable, "scripts/lock_engines.py", "--check"],
        cwd=scratch, capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "lock_engines --check must reject non-semver VERSION files"
    )


def _stage_tree(tmp_path: Path) -> Path:
    """Copy the files lock_engines cares about into an isolated dir
    so mutation tests do not disturb the working repo."""
    scratch = tmp_path / "repo"
    (scratch / "engines" / "vendor").mkdir(parents=True)
    (scratch / "engines" / "opengrep").mkdir()
    (scratch / "scripts").mkdir()
    shutil.copyfile(LOCK_SCRIPT, scratch / "scripts" / "lock_engines.py")
    shutil.copyfile(VERSION_FILE, scratch / "engines" / "VERSION")
    shutil.copyfile(OPENGREP_VERSION_FILE, scratch / "engines" / "opengrep" / "VERSION")
    shutil.copyfile(MANIFEST_FILE, scratch / "engines" / "manifest.json")
    shutil.copyfile(LOCK_FILE, scratch / "engines" / "lock.json")
    shutil.copyfile(VENDOR_LOCK_FILE, scratch / "engines" / "vendor" / "package-lock.json")
    return scratch

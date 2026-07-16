"""Supply-chain pins (finding B5): every workflow action is SHA-pinned and the
ci.yml opengrep install is version-pinned + checksum-verified.

A mutable action tag (`@v7`, `@release/v1`) lets an upstream tag move under the
release runners, including the `id-token: write` publish job. A `releases/latest`
executable fetched without a checksum is an unverified binary on PATH. Both are
remote policy for every `@latest` consumer, so they are asserted here rather than
only in a live run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO / ".github" / "workflows"

# `uses:` value up to the first whitespace (a trailing `# vX.Y.Z` comment is
# dropped by \S+). Matches both `- uses:` step form and job-level `uses:`.
_USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)", re.MULTILINE)
_PINNED_RE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _workflow_files() -> list[Path]:
    files = sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))
    assert files, f"no workflow files under {WORKFLOWS_DIR.relative_to(REPO)}"
    return files


def _uses_refs(text: str) -> list[str]:
    return _USES_RE.findall(text)


def test_every_action_use_is_sha_pinned():
    """No workflow may reference an action by a mutable tag or branch. Local
    `./` reusable-workflow refs are not SHA-pinnable and are exempt."""
    total = 0
    for wf in _workflow_files():
        for ref in _uses_refs(wf.read_text()):
            if ref.startswith("./"):
                continue
            total += 1
            assert _PINNED_RE.match(ref), (
                f"{wf.name}: `uses: {ref}` is not pinned to a 40-hex commit SHA "
                f"(bare @vN / @branch tags are mutable)"
            )
    # Guard against the regex silently matching nothing (e.g. dir moved).
    assert total >= 30, f"expected many pinned action uses, found {total}"


def test_no_bare_tag_or_branch_refs_remain():
    """Adversarial mirror: explicitly reject the exact mutable forms the fix
    replaced, so a reintroduced `@v7` / `@release/v1` fails even if the SHA
    regex above is ever loosened."""
    bad = re.compile(r"@(?:v\d[\w.\-]*|release/\S+|main|master|latest)(?:\s|$)")
    for wf in _workflow_files():
        for line in wf.read_text().splitlines():
            m = _USES_RE.match(line)
            if not m or m.group(1).startswith("./"):
                continue
            assert not bad.search(line), (
                f"{wf.name}: mutable action ref in `{line.strip()}`"
            )


def _opengrep_install_run(ci: dict) -> str:
    for step in ci["jobs"]["test"]["steps"]:
        if not isinstance(step, dict):
            continue
        run = str(step.get("run", ""))
        if "opengrep" in run and "curl" in run:
            return run
    raise AssertionError("ci.yml test job has no opengrep curl-install step")


@pytest.fixture(scope="module")
def ci_workflow() -> dict:
    ci = WORKFLOWS_DIR / "ci.yml"
    assert ci.is_file(), f"missing {ci.relative_to(REPO)}"
    return yaml.safe_load(ci.read_text())


def test_opengrep_fetch_is_version_pinned_not_latest(ci_workflow):
    """The opengrep binary must be fetched from a pinned version, never
    `releases/latest`. This fix sources the URL from engines/manifest.json so
    there is one source of truth with the shipped payload pin."""
    run = _opengrep_install_run(ci_workflow)
    assert "releases/latest" not in run, (
        "opengrep must not be fetched from releases/latest (unpinned version)"
    )
    versioned = "releases/download/v" in run
    manifest_sourced = "engines/manifest.json" in run and "source_url" in run
    assert versioned or manifest_sourced, (
        "opengrep fetch must target a versioned URL (or resolve source_url from "
        "engines/manifest.json), not a floating tag"
    )


def test_opengrep_verifies_sha256_before_chmod(ci_workflow):
    """The downloaded binary must have its sha256 verified BEFORE it is made
    executable. Verifying after chmod (or not at all) leaves an unverified
    binary runnable on PATH."""
    run = _opengrep_install_run(ci_workflow)
    lines = run.splitlines()
    verify_idx = next(
        (i for i, ln in enumerate(lines) if "sha256sum -c" in ln or "archive_sha256" in ln),
        None,
    )
    chmod_idx = next((i for i, ln in enumerate(lines) if "chmod" in ln), None)
    assert verify_idx is not None, (
        "opengrep install must verify a sha256 (sha256sum -c / archive_sha256)"
    )
    assert chmod_idx is not None, "opengrep install must chmod +x the binary"
    assert verify_idx < chmod_idx, (
        "sha256 verification must run BEFORE chmod +x (fail closed on mismatch)"
    )

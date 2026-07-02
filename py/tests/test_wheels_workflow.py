"""Wheels CI matrix spec: 5 platforms, per-platform runners, no cross-compile.

Pins the shape of `.github/workflows/wheels.yml` so a silent drop of any
platform (say, someone removing `linux-aarch64` because it's slow) turns
the test suite red before the wheels ever ship. On-disk fixture-tree
generation stays a runtime step (per handover #4); we assert the workflow
calls the materialize script rather than duplicating fixture files.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "wheels.yml"

REQUIRED_PLATFORMS = {
    "linux-x86_64",
    "linux-aarch64",
    "macos-aarch64",
    "windows-x86_64",
}

RUNNER_OS = {
    "linux-x86_64": "linux",
    "linux-aarch64": "linux",
    "macos-aarch64": "macos",
    "windows-x86_64": "windows",
}

ALLOWED_RUNNERS = {
    "linux-x86_64": {"ubuntu-22.04", "ubuntu-24.04"},
    "linux-aarch64": {"ubuntu-22.04-arm", "ubuntu-24.04-arm"},
    "macos-aarch64": {"macos-14", "macos-15"},
    "windows-x86_64": {"windows-2022", "windows-2019"},
}


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW.is_file(), f"missing {WORKFLOW.relative_to(REPO)}"
    return yaml.safe_load(WORKFLOW.read_text())


@pytest.fixture(scope="module")
def matrix_include(workflow) -> list[dict]:
    jobs = workflow["jobs"]
    assert "wheels" in jobs, "wheels.yml must define a job named 'wheels'"
    strategy = jobs["wheels"]["strategy"]
    include = strategy["matrix"]["include"]
    assert isinstance(include, list) and include, "matrix.include must be a non-empty list"
    return include


def test_matrix_covers_all_five_platforms(matrix_include):
    keys = {entry["platform"] for entry in matrix_include}
    missing = REQUIRED_PLATFORMS - keys
    extra = keys - REQUIRED_PLATFORMS
    assert not missing, f"matrix missing platforms: {sorted(missing)}"
    assert not extra, f"matrix has unexpected platforms: {sorted(extra)}"


def test_each_matrix_entry_declares_platform_key_and_runner(matrix_include):
    for entry in matrix_include:
        assert "platform" in entry, f"entry missing platform key: {entry}"
        assert "runner" in entry, f"entry missing runner: {entry}"
        assert entry["platform"] in REQUIRED_PLATFORMS


def test_no_cross_compilation_runner_matches_platform_os(matrix_include):
    """Each platform must map to a host-native runner. Cross-compiled binaries
    silently skip the wheel-vs-host doctor check the payload_sha256 pin catches."""
    for entry in matrix_include:
        runner = entry["runner"].lower()
        expected_os = RUNNER_OS[entry["platform"]]
        if expected_os == "linux":
            assert "ubuntu" in runner, f"linux platform {entry['platform']} must use ubuntu runner, got {runner}"
        elif expected_os == "macos":
            assert "macos" in runner, f"macos platform {entry['platform']} must use macos runner, got {runner}"
        elif expected_os == "windows":
            assert "windows" in runner, f"windows platform {entry['platform']} must use windows runner, got {runner}"


def test_runner_arch_matches_platform_arch(matrix_include):
    """Each platform must pin a runner whose architecture matches.

    GitHub's naming convention is asymmetric: `macos-13` is Intel, `macos-14+`
    are Apple Silicon (no `arm` substring). Silent cross-compile would land a
    wrong-arch payload under a right-tagged wheel. Whitelist per platform.
    """
    for entry in matrix_include:
        allowed = ALLOWED_RUNNERS[entry["platform"]]
        assert entry["runner"] in allowed, (
            f"platform {entry['platform']} runner {entry['runner']!r} not in "
            f"allowlist {sorted(allowed)} (would cross-compile silently)"
        )


def test_matrix_runners_are_all_distinct(matrix_include):
    runners = [entry["runner"] for entry in matrix_include]
    assert len(runners) == len(set(runners)), (
        f"two matrix cells share a runner label - would cross-compile: {runners}"
    )


def test_job_steps_invoke_build_and_materialize_scripts(workflow):
    """Fixture materialization must be a script call (not inline yaml)."""
    steps = workflow["jobs"]["wheels"]["steps"]
    script_calls = " ".join(str(s.get("run", "")) for s in steps if isinstance(s, dict))
    assert "scripts/build_wheels.py" in script_calls, (
        "wheels job must invoke scripts/build_wheels.py"
    )
    assert "scripts/materialize_fixture.py" in script_calls, (
        "wheels job must invoke scripts/materialize_fixture.py (on-disk fixture trees are banned)"
    )


def test_job_runs_doctor_and_lint_against_fixture(workflow):
    steps = workflow["jobs"]["wheels"]["steps"]
    step_text = " ".join(
        f"{s.get('name', '')} {s.get('run', '')}" for s in steps if isinstance(s, dict)
    )
    assert "tackbox doctor" in step_text, "wheels job must run `tackbox doctor` against fixture"
    assert "tackbox lint" in step_text, "wheels job must run `tackbox lint` against fixture"


def test_fail_fast_disabled_so_one_platform_break_does_not_hide_others(workflow):
    """A single-platform break must not cancel the other 4 in-flight jobs."""
    strategy = workflow["jobs"]["wheels"]["strategy"]
    assert strategy.get("fail-fast") is False, (
        "strategy.fail-fast must be explicitly false so all 5 platforms report"
    )

"""Publish workflow spec: OIDC, fat->thin sequential, skip-fat-if-unchanged,
non-cancelling concurrency; the fresh-runner canary lives in verify-release.yml.

Structural fixtures for `.github/workflows/publish.yml`. The workflow can
only be end-to-end exercised on a tag push into origin main (PyPI needs
Trusted Publishers configured on the account); until then, this file is
the executor-visible acceptance layer. Removing any contract below
should turn the suite red before publish.yml ships.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "publish.yml"
VERIFY_WORKFLOW = REPO / ".github" / "workflows" / "verify-release.yml"

REQUIRED_PLATFORMS = {
    "linux-x86_64",
    "linux-aarch64",
    "macos-aarch64",
    "windows-x86_64",
}


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW.is_file(), f"missing {WORKFLOW.relative_to(REPO)}"
    return yaml.safe_load(WORKFLOW.read_text())


@pytest.fixture(scope="module")
def verify_workflow() -> dict:
    assert VERIFY_WORKFLOW.is_file(), f"missing {VERIFY_WORKFLOW.relative_to(REPO)}"
    return yaml.safe_load(VERIFY_WORKFLOW.read_text())


def _steps_text(steps: list) -> str:
    parts = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        parts.append(str(s.get("name", "")))
        parts.append(str(s.get("run", "")))
        parts.append(str(s.get("uses", "")))
        parts.append(str(s.get("with", "")))
    return " ".join(parts)


# yaml.safe_load parses the bare `on:` key as Python True (YAML 1.1 bool);
# handle both forms so a future PyYAML fix does not falsely turn the suite red.
def _on(workflow: dict) -> dict:
    if "on" in workflow:
        return workflow["on"]
    if True in workflow:
        return workflow[True]
    raise AssertionError("workflow has no `on:` trigger")


def test_trigger_is_tag_push_only(workflow):
    """Publish must not fire on branch push or pull_request - only on release
    tags. Firing on branch push would race with ci.yml's tag creation and
    publish before the tag exists, or publish a non-tagged snapshot."""
    on = _on(workflow)
    assert "push" in on, "publish.yml must trigger on push"
    push = on["push"]
    assert "tags" in push and push["tags"], "publish must trigger on tag push"
    tags = push["tags"]
    assert any("v" in t for t in tags), (
        f"publish tag filter must accept semver v* tags, got {tags}"
    )
    assert "branches" not in push or not push["branches"], (
        "publish must not fire on branch push - only on tag"
    )
    assert "pull_request" not in on, "publish must not fire on pull_request"


def test_concurrency_group_publish_non_cancelling(workflow):
    """Two rapid tag pushes must serialize on Trusted Publisher, not race."""
    concurrency = workflow.get("concurrency")
    assert concurrency, "publish.yml must declare a concurrency group"
    assert concurrency.get("group") == "publish", (
        f"concurrency.group must be 'publish', got {concurrency.get('group')!r}"
    )
    assert concurrency.get("cancel-in-progress") is False, (
        "concurrency.cancel-in-progress must be false (never abort a mid-publish run)"
    )


def test_build_matrix_covers_all_five_platforms(workflow):
    jobs = workflow["jobs"]
    build_job = _find_matrix_job(jobs, REQUIRED_PLATFORMS)
    assert build_job is not None, (
        "publish.yml must include a matrix job covering all 5 target platforms"
    )
    include = build_job["strategy"]["matrix"]["include"]
    platforms = {e["platform"] for e in include}
    missing = REQUIRED_PLATFORMS - platforms
    assert not missing, f"build matrix missing platforms: {sorted(missing)}"


def test_build_step_passes_engines_version_from_file(workflow):
    """Fat wheel version must come from engines/VERSION so that a bump requires
    a repo edit under review, not an ad hoc yaml value."""
    jobs = workflow["jobs"]
    build_job = _find_matrix_job(jobs, REQUIRED_PLATFORMS)
    text = _steps_text(build_job["steps"])
    assert "engines/VERSION" in text, (
        "build steps must read engines/VERSION for the fat --engines-version"
    )
    assert "--engines-version" in text, (
        "build steps must forward --engines-version to build_wheels.py"
    )


def test_build_step_passes_tag_version(workflow):
    """Thin wheel version must be the pushed tag (with leading v stripped)."""
    jobs = workflow["jobs"]
    build_job = _find_matrix_job(jobs, REQUIRED_PLATFORMS)
    text = _steps_text(build_job["steps"])
    assert "github.ref_name" in text or "GITHUB_REF_NAME" in text, (
        "build steps must derive --version from the pushed tag"
    )
    assert "--version" in text


def test_lock_engines_check_runs_before_publish(workflow):
    """Drift check must fire before any wheel touches PyPI."""
    jobs = workflow["jobs"]
    all_step_text = " ".join(_steps_text(j.get("steps", [])) for j in jobs.values())
    assert "lock_engines.py" in all_step_text and "--check" in all_step_text, (
        "publish workflow must run `python scripts/lock_engines.py --check` "
        "before touching PyPI"
    )


def test_smoke_installs_built_wheels_into_fresh_venv(workflow):
    """Pre-publish smoke: install wheels off the artifact and run doctor+lint.
    Runs on all 5 platforms so a Windows-only wheel bug fails the release, not
    the consumer."""
    jobs = workflow["jobs"]
    smoke_job = _find_smoke_job(jobs)
    assert smoke_job is not None, "publish.yml must include a smoke job"
    include = smoke_job["strategy"]["matrix"]["include"]
    platforms = {e["platform"] for e in include}
    assert platforms == REQUIRED_PLATFORMS, (
        f"smoke matrix must cover all 5 platforms, got {sorted(platforms)}"
    )
    text = _steps_text(smoke_job["steps"])
    assert "tackbox doctor" in text, "smoke must run tackbox doctor"
    assert "tackbox lint" in text, "smoke must run tackbox lint"
    assert "materialize_fixture.py" in text, (
        "smoke must materialize the shared fixture, not roll its own"
    )


def test_publish_fat_job_uses_oidc_no_tokens(workflow):
    fat = _publish_fat_job(workflow["jobs"])
    perms = fat.get("permissions", {})
    assert perms.get("id-token") == "write", (
        "publish-fat must request id-token: write (OIDC Trusted Publisher)"
    )
    text = _steps_text(fat["steps"])
    assert "pypa/gh-action-pypi-publish" in text, (
        "publish-fat must use pypa/gh-action-pypi-publish (Trusted Publishers)"
    )
    lowered = text.lower()
    assert "twine" not in lowered, "publish-fat must not shell out to twine"
    assert "password" not in lowered, (
        "publish-fat must not carry a password/token; OIDC only"
    )


def test_publish_thin_job_uses_oidc_no_tokens(workflow):
    thin = _publish_thin_job(workflow["jobs"])
    perms = thin.get("permissions", {})
    assert perms.get("id-token") == "write", (
        "publish-thin must request id-token: write (OIDC Trusted Publisher)"
    )
    text = _steps_text(thin["steps"])
    assert "pypa/gh-action-pypi-publish" in text, (
        "publish-thin must use pypa/gh-action-pypi-publish"
    )
    lowered = text.lower()
    assert "twine" not in lowered
    assert "password" not in lowered


def test_publish_fat_and_thin_use_distinct_environments(workflow):
    """PyPI Trusted Publishers rejects two pending TPs with the same
    (owner, repo, workflow, environment) tuple - the OIDC subject cannot
    disambiguate between project names. If both jobs share an environment,
    only one of tackbox / tackbox-engines can be pending at a time.
    Each publish job must live in its own environment."""
    fat = _publish_fat_job(workflow["jobs"])
    thin = _publish_thin_job(workflow["jobs"])
    fat_env = fat.get("environment")
    thin_env = thin.get("environment")
    assert fat_env, "publish-fat must declare an environment for OIDC subject scoping"
    assert thin_env, "publish-thin must declare an environment for OIDC subject scoping"
    assert fat_env != thin_env, (
        f"publish-fat and publish-thin must use distinct environments; "
        f"both are {fat_env!r}. PyPI pending TP would collide."
    )


def test_publish_thin_depends_on_publish_fat(workflow):
    """Thin pins tackbox-engines==X in Requires-Dist. If fat isn't up on PyPI
    when thin is uploaded, `uv pip install tackbox` cannot resolve. Sequential."""
    thin = _publish_thin_job(workflow["jobs"])
    needs = thin.get("needs") or []
    if isinstance(needs, str):
        needs = [needs]
    fat_job_name = _publish_fat_job_name(workflow["jobs"])
    assert fat_job_name in needs, (
        f"publish-thin must declare needs: {fat_job_name} so thin never publishes "
        f"before fat resolves"
    )


def test_publish_jobs_depend_on_smoke(workflow):
    """Broken wheels must not reach PyPI - `@latest` propagates instantly to
    consumers, and PyPI never truly deletes releases (yank only)."""
    fat = _publish_fat_job(workflow["jobs"])
    thin = _publish_thin_job(workflow["jobs"])
    smoke_name = _smoke_job_name(workflow["jobs"])
    for job_name, job in (("publish-fat", fat), ("publish-thin", thin)):
        needs = job.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        transitive = _collect_transitive_needs(workflow["jobs"], job_name)
        assert smoke_name in transitive, (
            f"{job_name} must transitively depend on {smoke_name} - "
            f"unsmoked wheels must never reach PyPI"
        )


def test_publish_fat_skips_when_version_already_on_pypi(workflow):
    """When engines/VERSION already exists on PyPI, fat publish must be a
    no-op so a thin-only patch bump does not 409 on the fat republish.
    Enforced via the pypa action's skip-existing input, which turns the
    409 into success at upload time - simpler than a pre-check probe and
    removes shell interpolation of engines/VERSION into a python -c."""
    fat = _publish_fat_job(workflow["jobs"])
    for step in fat["steps"]:
        if not isinstance(step, dict):
            continue
        if "pypa/gh-action-pypi-publish" not in str(step.get("uses", "")):
            continue
        with_args = step.get("with") or {}
        assert with_args.get("skip-existing") is True, (
            "publish-fat's pypa publish step must set skip-existing: true so "
            "an identical fat republish (thin-only patch bump) succeeds "
            "instead of crashing on a PyPI 409"
        )
        return
    raise AssertionError("no pypa/gh-action-pypi-publish step found in publish-fat")


def test_publish_thin_does_not_set_skip_existing(workflow):
    """Thin version comes from the pushed tag; a duplicate publish means the
    engineer rebased or re-tagged. That is a workflow bug, not a silent-skip
    case - 409 loud fail is the correct signal."""
    thin = _publish_thin_job(workflow["jobs"])
    for step in thin["steps"]:
        if not isinstance(step, dict):
            continue
        if "pypa/gh-action-pypi-publish" not in str(step.get("uses", "")):
            continue
        with_args = step.get("with") or {}
        assert with_args.get("skip-existing") is not True, (
            "publish-thin must not skip-existing: a duplicate thin version "
            "signals a tag rebase or force-push and must fail loudly"
        )
        return
    raise AssertionError("no pypa/gh-action-pypi-publish step found in publish-thin")


def test_canary_matrix_covers_required_platforms(verify_workflow):
    canary = _canary_job(verify_workflow["jobs"])
    assert canary is not None, (
        "verify-release.yml must run a fresh-runner canary on all platforms"
    )
    include = canary["strategy"]["matrix"]["include"]
    platforms = {e["platform"] for e in include}
    assert platforms == REQUIRED_PLATFORMS, (
        f"canary must cover {sorted(REQUIRED_PLATFORMS)}, got {sorted(platforms)}"
    )


def test_canary_uses_uvx_from_pypi(verify_workflow):
    """Step-6c acceptance, relocated: a fresh runner with an empty uv cache
    can `uvx tackbox@<VERSION> lint .` and match the local dev build."""
    canary = _canary_job(verify_workflow["jobs"])
    text = _steps_text(canary["steps"])
    assert "uvx --refresh" in text, "canary must uvx-install from PyPI"
    assert "materialize_fixture.py" in text, (
        "canary must materialize the shared fixture on the fresh runner"
    )
    target_text = _steps_text(verify_workflow["jobs"]["target"]["steps"])
    assert "tackbox@" in target_text, (
        "target job must build the uvx tackbox@<ver|latest> spec"
    )


def test_canary_runs_after_publish_and_on_schedule(verify_workflow):
    """Ordering vs publish comes from the workflow_run trigger; the cron leg
    keeps @latest verified between releases."""
    on = _on(verify_workflow)
    wr = on.get("workflow_run") or {}
    assert "publish" in (wr.get("workflows") or []), (
        "verify-release must trigger on completion of the publish workflow"
    )
    assert "completed" in (wr.get("types") or []), (
        "verify-release workflow_run trigger must fire on completed runs"
    )
    assert "schedule" in on, "verify-release must also run on a cron schedule"
    assert "workflow_dispatch" in on, (
        "verify-release must be manually dispatchable for reruns"
    )


def test_publish_has_no_pypi_smoke(workflow):
    """The canary moved to verify-release.yml; publish must not wait on PyPI
    CDN convergence."""
    for name, job in workflow["jobs"].items():
        text = _steps_text((job or {}).get("steps", []))
        assert not ("uvx" in text and "tackbox@" in text), (
            f"publish job {name!r} installs from PyPI - that belongs to "
            f"verify-release.yml"
        )


def _find_matrix_job(jobs: dict, required_platforms: set) -> dict | None:
    """Locate the matrix job whose matrix.include lists all required platforms
    and that actually invokes build_wheels.py (i.e., the wheels build).
    Distinct from the smoke matrix and the post-publish smoke matrix."""
    candidates = []
    for name, job in jobs.items():
        strategy = (job or {}).get("strategy") or {}
        matrix = strategy.get("matrix") or {}
        include = matrix.get("include") or []
        if not include or not isinstance(include, list):
            continue
        try:
            platforms = {e["platform"] for e in include}
        except (KeyError, TypeError):
            # no-report: malformed matrix entry in the scanned workflow yaml - skip it
            continue
        if platforms != required_platforms:
            continue
        text = _steps_text(job.get("steps", []))
        if "build_wheels.py" in text:
            candidates.append((name, job))
    assert len(candidates) == 1, (
        f"expected exactly one build matrix job, got {[n for n, _ in candidates]}"
    )
    return candidates[0][1]


def _find_smoke_job(jobs: dict) -> dict | None:
    for name, job in jobs.items():
        text = _steps_text((job or {}).get("steps", []))
        if "tackbox doctor" in text and "tackbox lint" in text and "uvx" not in text:
            return job
    return None


def _pypi_publish_packages_dir(job: dict) -> str | None:
    """Extract the packages-dir arg of the pypa/gh-action-pypi-publish step
    in this job, or None if no publish step exists. Publish-fat and
    publish-thin are distinguished by whether packages-dir points at the
    fat or thin isolate directory."""
    for step in (job or {}).get("steps", []):
        if not isinstance(step, dict):
            continue
        if "pypa/gh-action-pypi-publish" in str(step.get("uses", "")):
            with_args = step.get("with") or {}
            if isinstance(with_args, dict):
                return with_args.get("packages-dir")
    return None


def _publish_fat_job(jobs: dict) -> dict:
    for name, job in jobs.items():
        if _pypi_publish_packages_dir(job) == "fat":
            return job
    raise AssertionError("no publish job found for fat (tackbox_engines) wheels")


def _publish_fat_job_name(jobs: dict) -> str:
    for name, job in jobs.items():
        if _pypi_publish_packages_dir(job) == "fat":
            return name
    raise AssertionError("no publish job found for fat wheels")


def _publish_thin_job(jobs: dict) -> dict:
    for name, job in jobs.items():
        if _pypi_publish_packages_dir(job) == "thin":
            return job
    raise AssertionError("no publish job found for thin (tackbox) wheels")


def _publish_thin_job_name(jobs: dict) -> str:
    for name, job in jobs.items():
        if _pypi_publish_packages_dir(job) == "thin":
            return name
    raise AssertionError("no publish job found for thin wheels")


def _smoke_job_name(jobs: dict) -> str:
    for name, job in jobs.items():
        text = _steps_text((job or {}).get("steps", []))
        if "tackbox doctor" in text and "tackbox lint" in text and "uvx" not in text:
            return name
    raise AssertionError("no pre-publish smoke job found")


def _canary_job(jobs: dict) -> dict | None:
    for name, job in jobs.items():
        text = _steps_text((job or {}).get("steps", []))
        if "uvx --refresh" in text:
            return job
    return None


def _collect_transitive_needs(jobs: dict, root: str) -> set:
    """Follow needs: chains transitively so `smoke -> publish-fat -> publish-thin`
    counts as publish-thin depending on smoke, even if the yaml only spells the
    direct edge."""
    visited = set()
    stack = [root]
    while stack:
        name = stack.pop()
        if name in visited:
            continue
        visited.add(name)
        job = jobs.get(name) or {}
        needs = job.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        stack.extend(needs)
    visited.discard(root)
    return visited

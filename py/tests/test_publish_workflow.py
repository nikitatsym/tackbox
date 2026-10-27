"""Publish workflow spec: OIDC, payloads->thin sequential, skip-payload-if-
unchanged, non-cancelling concurrency; the fresh-runner canary lives in
verify-release.yml.

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

HOST_PLATFORMS = {
    "linux-x86_64",
    "linux-aarch64",
    "macos-aarch64",
    "windows-x86_64",
}
ANDROID = "android-aarch64"
# Android cross-builds with the others but is smoked and canaried in Termux.
REQUIRED_PLATFORMS = HOST_PLATFORMS | {ANDROID}


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


def test_build_step_passes_opengrep_version_from_file(workflow):
    """The tackbox-opengrep version comes from engines/opengrep/VERSION, a
    reviewed repo edit like engines/VERSION."""
    build_job = _find_matrix_job(workflow["jobs"], REQUIRED_PLATFORMS)
    text = _steps_text(build_job["steps"])
    assert "engines/opengrep/VERSION" in text
    assert "--opengrep-version" in text


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
    Runs on all host platforms so a Windows-only wheel bug fails the release,
    not the consumer."""
    jobs = workflow["jobs"]
    smoke_job = _find_smoke_job(jobs)
    assert smoke_job is not None, "publish.yml must include a smoke job"
    include = smoke_job["strategy"]["matrix"]["include"]
    platforms = {e["platform"] for e in include}
    assert platforms == HOST_PLATFORMS, (
        f"smoke matrix must cover the host platforms, got {sorted(platforms)}"
    )
    text = _steps_text(smoke_job["steps"])
    assert "tackbox doctor" in text, "smoke must run tackbox doctor"
    assert "tackbox lint" in text, "smoke must run tackbox lint"
    assert "materialize_fixture.py" in text, (
        "smoke must materialize the shared fixture, not roll its own"
    )


def test_smoke_runs_hook_protocol_against_the_built_artifact(workflow):
    smoke = _find_smoke_job(workflow["jobs"])
    assert smoke is not None
    steps = [
        step
        for step in smoke["steps"]
        if isinstance(step, dict) and step.get("name") == "hook-protocol artifact canary"
    ]
    assert len(steps) == 1, "the built wheel must exercise hook-protocol"
    text = _steps_text(steps)
    assert "succeeded" in text and "hook-protocol" in text


@pytest.mark.parametrize("packages_dir", ["fat", "opengrep"])
def test_payload_publish_job_uses_oidc_no_tokens(workflow, packages_dir):
    job = _publish_job(workflow["jobs"], packages_dir)[1]
    perms = job.get("permissions", {})
    assert perms.get("id-token") == "write", (
        f"the {packages_dir} publish job must request id-token: write (OIDC Trusted Publisher)"
    )
    text = _steps_text(job["steps"])
    assert "pypa/gh-action-pypi-publish" in text, (
        f"the {packages_dir} publish job must use pypa/gh-action-pypi-publish (Trusted Publishers)"
    )
    lowered = text.lower()
    assert "twine" not in lowered, "payload publish must not shell out to twine"
    assert "password" not in lowered, (
        "payload publish must not carry a password/token; OIDC only"
    )


def test_android_wheels_are_smoked_in_termux(workflow):
    """The Android wheels cross-build on x86_64; an arm64 runner smokes them in
    the pinned Termux image (an Android userland): thin alone plus the staged
    payloads, hook-protocol, doctor and lint, like the host smoke."""
    job = workflow["jobs"]["smoke-android"]
    assert job["needs"] == "build"
    assert job["runs-on"] in {"ubuntu-22.04-arm", "ubuntu-24.04-arm"}
    text = _steps_text(job["steps"])
    assert "wheels-android-aarch64" in text
    assert "termux/termux-docker:aarch64@sha256:" in text  # digest: test_workflow_pins
    assert "scripts/termux_smoke.sh provision" in text
    assert "scripts/termux_smoke.sh wheels" in text


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


def test_publish_jobs_use_distinct_environments(workflow):
    """PyPI Trusted Publishers rejects two pending TPs with the same
    (owner, repo, workflow, environment) tuple - the OIDC subject cannot
    disambiguate between project names. If two jobs share an environment,
    only one of tackbox / tackbox-engines / tackbox-opengrep can be pending
    at a time. Each publish job must live in its own environment."""
    jobs = workflow["jobs"]
    envs = {
        packages_dir: _publish_job(jobs, packages_dir)[1].get("environment")
        for packages_dir in ("fat", "opengrep", "thin")
    }
    assert all(envs.values()), f"every publish job must declare an environment: {envs}"
    assert len(set(envs.values())) == 3, (
        f"publish jobs must use distinct environments, got {envs}. PyPI pending TP would collide."
    )
    assert envs["opengrep"] == "pypi-opengrep"


def test_publish_thin_depends_on_both_payload_publishes(workflow):
    """Thin pins no payload in Requires-Dist (F6), but the runtime store
    fetches both payload wheels from PyPI on first use. If thin ships before a
    payload is up, the first `uvx tackbox` (and the canary) cannot resolve it.
    Sequential payloads->thin keeps every fetch target present."""
    thin = _publish_thin_job(workflow["jobs"])
    needs = thin.get("needs") or []
    if isinstance(needs, str):
        needs = [needs]
    for packages_dir in ("fat", "opengrep"):
        job_name = _publish_job(workflow["jobs"], packages_dir)[0]
        assert job_name in needs, (
            f"publish-thin must declare needs: {job_name} so thin never publishes "
            f"before that payload resolves"
        )


def test_publish_jobs_depend_on_smoke(workflow):
    """Broken wheels must not reach PyPI - `@latest` propagates instantly to
    consumers, and PyPI never truly deletes releases (yank only)."""
    fat = _publish_fat_job(workflow["jobs"])
    thin = _publish_thin_job(workflow["jobs"])
    smoke_names = {_smoke_job_name(workflow["jobs"]), "smoke-android"}
    opengrep = _publish_job(workflow["jobs"], "opengrep")[1]
    for job_name, job in (("publish-fat", fat), ("publish-opengrep", opengrep), ("publish-thin", thin)):
        needs = job.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        transitive = _collect_transitive_needs(workflow["jobs"], job_name)
        assert smoke_names <= transitive, (
            f"{job_name} must transitively depend on {sorted(smoke_names)} - "
            f"unsmoked wheels must never reach PyPI"
        )


@pytest.mark.parametrize("packages_dir", ["fat", "opengrep"])
def test_payload_publish_skips_when_version_already_on_pypi(workflow, packages_dir):
    """When a payload version already exists on PyPI, its publish must be a
    no-op so a thin-only patch bump does not 409 on the payload republish.
    Enforced via the pypa action's skip-existing input, which turns the
    409 into success at upload time - simpler than a pre-check probe and
    removes shell interpolation of the VERSION into a python -c."""
    with_args = _pypi_publish_with(_publish_job(workflow["jobs"], packages_dir)[1])
    assert with_args is not None, f"no pypa/gh-action-pypi-publish step for {packages_dir}"
    assert with_args.get("skip-existing") is True, (
        f"the {packages_dir} pypa publish step must set skip-existing: true so "
        "an identical payload republish (thin-only patch bump) succeeds "
        "instead of crashing on a PyPI 409"
    )


@pytest.mark.parametrize(
    ("packages_dir", "version_file", "payload_flag"),
    [("fat", "engines/VERSION", "--payload engines"),
     ("opengrep", "engines/opengrep/VERSION", "--payload opengrep")],
)
def test_payload_publish_runs_integrity_guard_before_publish(
    workflow, packages_dir, version_file, payload_flag
):
    """skip-existing keeps the OLD wheel when a payload input changed without a
    VERSION bump, while the new thin ships pointing at the NEW payload
    tree-sha - bricking every fresh install. A pre-publish guard compares the
    local payload against any already-published same-version wheel and hard-
    fails on a mismatch. It must run BEFORE the publish action (post-publish is
    too late - skip-existing has already kept the stale wheel), and skip-existing
    must stay set: the guard is what makes the idempotent re-run safe."""
    job = _publish_job(workflow["jobs"], packages_dir)[1]
    steps = job["steps"]
    guard_idx = _guard_step_index(steps)
    assert guard_idx is not None, (
        f"the {packages_dir} publish job must run scripts/guard_engines_publish.py before publishing"
    )
    publish_idx = _pypi_publish_step_index(steps)
    assert publish_idx is not None, f"the {packages_dir} job has no pypa/gh-action-pypi-publish step"
    assert guard_idx < publish_idx, (
        "the integrity guard must run BEFORE the pypa publish action"
    )
    with_args = _pypi_publish_with(job)
    assert with_args is not None and with_args.get("skip-existing") is True, (
        "skip-existing must remain set; the pre-publish guard is what makes an "
        "idempotent payload re-upload safe"
    )
    guard_text = _steps_text([steps[guard_idx]])
    assert version_file in guard_text, (
        f"guard must resolve the payload version from {version_file}, not an ad hoc value"
    )
    assert payload_flag in guard_text and f"--wheel-dir {packages_dir}" in guard_text


def test_publish_thin_does_not_set_skip_existing(workflow):
    """Thin version comes from the pushed tag; a duplicate publish means the
    engineer rebased or re-tagged. That is a workflow bug, not a silent-skip
    case - 409 loud fail is the correct signal."""
    with_args = _pypi_publish_with(_publish_thin_job(workflow["jobs"]))
    assert with_args is not None, "no pypa/gh-action-pypi-publish step in publish-thin"
    assert with_args.get("skip-existing") is not True, (
        "publish-thin must not skip-existing: a duplicate thin version "
        "signals a tag rebase or force-push and must fail loudly"
    )


def test_canary_matrix_covers_required_platforms(verify_workflow):
    canary = _canary_job(verify_workflow["jobs"])
    assert canary is not None, (
        "verify-release.yml must run a fresh-runner canary on all platforms"
    )
    include = canary["strategy"]["matrix"]["include"]
    platforms = {e["platform"] for e in include}
    assert platforms == HOST_PLATFORMS, (
        f"canary must cover {sorted(HOST_PLATFORMS)}, got {sorted(platforms)}"
    )


def test_android_canary_runs_uvx_in_termux_and_gates_the_verdict(verify_workflow):
    """uvx resolves the Android wheel from PyPI in the pinned Termux image, and
    the badge and commit status count it like the host canary."""
    jobs = verify_workflow["jobs"]
    canary = jobs["canary-android"]
    assert canary["needs"] == "target"
    assert canary["runs-on"] in {"ubuntu-22.04-arm", "ubuntu-24.04-arm"}
    text = _steps_text(canary["steps"])
    assert "termux/termux-docker:aarch64@sha256:" in text
    assert "scripts/termux_smoke.sh provision" in text
    assert "scripts/termux_smoke.sh uvx" in text
    envs = [s.get("env") or {} for s in canary["steps"] if isinstance(s, dict)]
    assert {"VERSION": "${{ needs.target.outputs.version }}"} in envs
    for name in ("badge", "status"):
        job = jobs[name]
        assert "canary-android" in job["needs"], name
        assert "needs.canary-android.result" in job["if"], name
        result = str(job["steps"][0]["env"]["RESULT"])
        assert "needs.canary.result" in result and "needs.canary-android.result" in result, name


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


def test_canary_convergence_gate_is_the_real_command(verify_workflow):
    """A separate resolve probe proves nothing about the next request (CDN
    edges flip-flop while the index converges): the doctor loop itself is the
    gate, retrying only when the exact version or its matching platform wheel
    is not yet visible. Lint reuses the resolved tool env, so there is no second
    resolution to race."""
    canary = _canary_job(verify_workflow["jobs"])
    text = _steps_text(canary["steps"])
    assert "uv pip compile" not in text, (
        "no separate resolve probe: the doctor loop is the convergence gate"
    )
    assert "No solution found" in text and "no version of" in text, (
        "doctor must retry when the exact release is not yet visible"
    )
    assert "has no wheels with a matching platform tag" in text, (
        "doctor must retry when a partially converged release omits this platform wheel"
    )
    lint_runs = [
        str(s.get("run", ""))
        for s in canary["steps"]
        if isinstance(s, dict) and "lint" in str(s.get("name", ""))
    ]
    assert lint_runs and "--refresh" not in lint_runs[0], (
        "lint must reuse the tool env doctor resolved (no second resolution)"
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


def test_publish_verifies_the_exact_thin_wheel_before_release(workflow):
    """The fresh resolver canary is a release dependency, not an async report."""
    jobs = workflow["jobs"]
    canary = jobs.get("verify-thin")
    assert canary is not None, "publish must contain an exact-wheel canary"
    assert canary.get("needs") == "publish-thin"
    text = _steps_text(canary.get("steps", []))
    assert "uvx --refresh" in text and "tackbox@${VERSION#v}" in text
    assert "hook-protocol" in text
    assert "has no wheels with a matching platform tag" in text, (
        "exact-wheel canary must retry a partially converged release"
    )
    assert "actions/checkout" not in text, "canary must use a fresh runner"
    release = jobs.get("github-release")
    assert release is not None and release.get("needs") == "verify-thin"


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


def _guard_step_index(steps: list) -> int | None:
    for i, step in enumerate(steps):
        if isinstance(step, dict) and "guard_engines_publish.py" in str(step.get("run", "")):
            return i
    return None


def _pypi_publish_step_index(steps: list) -> int | None:
    for i, step in enumerate(steps):
        if isinstance(step, dict) and "pypa/gh-action-pypi-publish" in str(step.get("uses", "")):
            return i
    return None


def _pypi_publish_with(job: dict) -> dict | None:
    """The `with:` args of this job's pypa/gh-action-pypi-publish step, or None
    when the job has no such step."""
    for step in (job or {}).get("steps", []):
        if not isinstance(step, dict):
            continue
        if "pypa/gh-action-pypi-publish" in str(step.get("uses", "")):
            with_args = step.get("with") or {}
            return with_args if isinstance(with_args, dict) else {}
    return None


def _pypi_publish_packages_dir(job: dict) -> str | None:
    """The packages-dir arg of the job's pypa publish step - fat, opengrep and
    thin are distinguished by which isolate dir it points at."""
    with_args = _pypi_publish_with(job)
    return with_args.get("packages-dir") if with_args is not None else None


def _publish_job(jobs: dict, packages_dir: str) -> tuple[str, dict]:
    """(name, job) of the publish job whose pypa step uploads packages_dir:
    `fat` (tackbox_engines), `opengrep` (tackbox_opengrep), `thin` (tackbox)."""
    for name, job in jobs.items():
        if _pypi_publish_packages_dir(job) == packages_dir:
            return name, job
    raise AssertionError(f"no publish job found for {packages_dir} wheels")


def _publish_fat_job(jobs: dict) -> dict:
    return _publish_job(jobs, "fat")[1]


def _publish_thin_job(jobs: dict) -> dict:
    return _publish_job(jobs, "thin")[1]


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

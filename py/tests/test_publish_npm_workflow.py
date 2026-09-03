"""npm publish workflow spec: OIDC Trusted Publishing, provenance, no stored
token, verified-tag versioning, and release ordering.

Structural fixtures for `.github/workflows/publish-npm.yml` plus a live
`npm pack --dry-run` file-set check. The workflow can only be end-to-end
exercised after the npmjs.com Trusted Publisher is configured; until then this
file is the executor-visible acceptance layer. Removing any contract below
should turn the suite red before publish-npm.yml ships.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

# Reuse the generic workflow-YAML helpers from the PyPI publish-workflow suite
# rather than copying them (the repo's own DUP001 rule flags the paste).
from test_publish_workflow import _on, _steps_text

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "publish-npm.yml"
PUBLISH_WORKFLOW = REPO / ".github" / "workflows" / "publish.yml"
PACKAGE_JSON = REPO / "package.json"


@pytest.fixture(scope="module")
def workflow_text() -> str:
    assert WORKFLOW.is_file(), f"missing {WORKFLOW.relative_to(REPO)}"
    return WORKFLOW.read_text()


@pytest.fixture(scope="module")
def workflow(workflow_text: str) -> dict:
    return yaml.safe_load(workflow_text)


def _strip_comments(text: str) -> str:
    """Drop YAML comments so token-reference checks read the *effective*
    workflow, not prose. A functional token must appear on a non-comment line;
    an explanatory `# ... no NODE_AUTH_TOKEN ...` note must not trip the check.
    No `#` appears inside a quoted scalar in this workflow, so the naive
    inline-comment cut is safe here."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if " #" in line:
            line = line[: line.index(" #")]
        out.append(line)
    return "\n".join(out)


def _publish_job(workflow: dict) -> dict:
    """The single job that runs `npm publish`."""
    for job in workflow["jobs"].values():
        if "npm publish" in _steps_text((job or {}).get("steps", [])):
            return job
    raise AssertionError("no job runs `npm publish`")


def test_trigger_waits_for_completed_publish_workflow(workflow):
    """npm must wait for the successful PyPI workflow, not race the tag push."""
    on = _on(workflow)
    workflow_run = on.get("workflow_run")
    assert workflow_run is not None, "publish-npm.yml must trigger on workflow_run"
    assert workflow_run.get("workflows") == ["publish"], (
        "npm must wait for the PyPI publish workflow"
    )
    assert workflow_run.get("types") == ["completed"], (
        "npm must evaluate the PyPI workflow's final result"
    )
    assert "push" not in on, "independent tag pushes race PyPI availability"
    assert "pull_request" not in on, "must not fire on pull_request"


def test_publish_job_accepts_only_successful_tagged_push_runs(workflow):
    job = _publish_job(workflow)
    condition = str(job.get("if", ""))
    assert "workflow_run.conclusion" in condition and "'success'" in condition
    assert "workflow_run.event" in condition and "'push'" in condition
    assert "workflow_run.head_branch" in condition
    assert "startsWith" in condition, "only release tags may publish npm"


def test_publish_job_requests_oidc_id_token(workflow):
    """Trusted Publishing + provenance both need a GitHub OIDC token."""
    job = _publish_job(workflow)
    perms = job.get("permissions", {})
    assert perms.get("id-token") == "write", (
        "publish job must request id-token: write for npm Trusted Publishing"
    )
    assert perms.get("contents") == "read", (
        "publish job should request contents: read (least privilege for checkout)"
    )


def test_publish_uses_provenance_and_public_access(workflow):
    """The published artifact must carry a signed provenance attestation and go
    to the public registry under the unscoped name."""
    job = _publish_job(workflow)
    text = _steps_text(job["steps"])
    assert "npm publish" in text, "publish job must run `npm publish`"
    assert "--provenance" in text, "npm publish must pass --provenance"
    assert "--access public" in text, "npm publish must pass --access public"


def test_no_long_lived_npm_token(workflow_text):
    """OIDC only: no NODE_AUTH_TOKEN, no NPM_TOKEN, no secrets whatsoever. A
    stored token is exactly what Trusted Publishing exists to eliminate."""
    lowered = _strip_comments(workflow_text).lower()
    assert "node_auth_token" not in lowered, (
        "publish-npm.yml must not reference NODE_AUTH_TOKEN - OIDC only"
    )
    assert "npm_token" not in lowered, (
        "publish-npm.yml must not reference an NPM_TOKEN - OIDC only"
    )
    assert "secrets." not in workflow_text, (
        "publish-npm.yml must consume no secrets - Trusted Publishing needs none"
    )


def test_version_comes_from_the_completed_release_run_tag(workflow):
    """The published version must come from the successful release run's tag."""
    job = _publish_job(workflow)
    text = _steps_text(job["steps"])
    assert "npm pkg set version" in text or "npm version" in text, (
        "publish job must write the resolved version into package.json"
    )
    version_steps = [
        step
        for step in job["steps"]
        if isinstance(step, dict) and step.get("id") == "ver"
    ]
    assert len(version_steps) == 1, "publish job must resolve one release version"
    version_step = version_steps[0]
    tag = str(version_step.get("env", {}).get("TAG", ""))
    assert "workflow_run.head_branch" in tag
    assert "${TAG#v}" in version_step.get("run", "")


def test_checkout_uses_immutable_completed_release_source(workflow):
    job = _publish_job(workflow)
    checkouts = [
        step
        for step in job["steps"]
        if isinstance(step, dict) and str(step.get("uses", "")).startswith("actions/checkout")
    ]
    assert len(checkouts) == 1, "npm must publish the release workflow's source"
    ref = str(checkouts[0].get("with", {}).get("ref", ""))
    assert "workflow_run.head_sha" in ref


def test_pypi_hook_protocol_canary_completes_before_npm_trigger():
    publish = yaml.safe_load(PUBLISH_WORKFLOW.read_text())
    canary = publish["jobs"].get("verify-thin")
    assert canary is not None, "publish must verify the thin wheel before npm"
    assert canary.get("needs") == "publish-thin"
    text = _steps_text(canary.get("steps", []))
    assert "uvx --refresh" in text and "hook-protocol" in text


def test_npm_is_trusted_publishing_capable(workflow):
    """Trusted Publishing needs npm >= 11.5.1. setup-node's bundled npm is not
    guaranteed that new, so the workflow must force a capable npm."""
    job = _publish_job(workflow)
    text = _steps_text(job["steps"])
    assert "actions/setup-node" in text, "publish job must use actions/setup-node"
    assert "npm install -g npm" in text, (
        "publish job must upgrade to a Trusted-Publishing-capable npm (>= 11.5.1)"
    )


def test_published_tarball_excludes_test_suites():
    """The npm tarball must ship the plugin/rules/report/bin/preset payload but
    NOT js/tests/*.test.js (dev-only, run via `npm test`). `files: ["js/"]`
    used to ship the whole tree; the tightened allowlist must keep tests out."""
    npm = shutil.which("npm")
    if npm is not None:
        proc = subprocess.run(
            [npm, "pack", "--dry-run", "--json"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        )
        entries = json.loads(proc.stdout)
        # npm <= 11 prints a list of packages; npm 12 keys them by name.
        if isinstance(entries, dict):
            entries = list(entries.values())
        paths = {f["path"] for f in entries[0]["files"]}
        leaked = sorted(p for p in paths if p.startswith("js/tests/"))
        assert not leaked, f"npm tarball must not ship test suites: {leaked}"
        for required in (
            "js/eslint-plugin.js",
            "js/report.js",
            "js/omp/index.mjs",
            "js/omp/index.js",
            "js/omp/hook.js",
            "js/omp/payload.js",
            "bin/tackbox-eslint.js",
            "bin/tackbox-mdlint.js",
            "eslint.config.preset.js",
        ):
            assert required in paths, f"npm tarball missing required file {required}"
        assert any(p.startswith("js/rules/") for p in paths), (
            "npm tarball must ship the js/rules/ implementations"
        )
    else:
        # npm not on PATH: assert the files allowlist itself cannot pull in
        # tests. `files` is a strict allowlist that .npmignore cannot trim, so a
        # blanket `js/` entry would re-ship js/tests/; enumerated subpaths must
        # be used and none may name a tests path.
        pkg = json.loads(PACKAGE_JSON.read_text())
        files = pkg.get("files", [])
        assert files, "package.json must declare a files allowlist"
        assert "js/" not in files and "js" not in files, (
            "files must not blanket-include js/ (that re-ships js/tests/); "
            "enumerate js/ subpaths instead"
        )
        assert not any("test" in entry for entry in files), (
            f"files allowlist must not name a tests path: {files}"
        )


def test_omp_extension_manifest_resolves_on_disk():
    """`omp plugin install tackbox` loads the extension through this manifest
    entry: a stale or misspelled path installs a plugin that registers nothing.
    The entry must be one of the files the tarball ships (above)."""
    pkg = json.loads(PACKAGE_JSON.read_text())
    entries = pkg.get("omp", {}).get("extensions")
    assert entries == ["./js/omp/index.mjs"], f"unexpected omp manifest: {entries}"
    for entry in entries:
        assert (REPO / entry).is_file(), f"omp extension entry does not exist: {entry}"
    for required in (
        "js/omp/index.mjs",
        "js/omp/index.js",
        "js/omp/hook.js",
        "js/omp/payload.js",
    ):
        assert required in pkg.get("files", []), f"files allowlist omits {required}"

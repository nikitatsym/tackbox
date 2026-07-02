"""CI workflow invariants: auto-tag must use a non-GITHUB_TOKEN identity so
publish.yml wakes on the pushed tag.

GitHub's documented anti-recursion rule: a tag pushed by the default
GITHUB_TOKEN does not trigger downstream workflows. The tag job in
ci.yml therefore checks out with `ssh-key: ${{ secrets.RELEASE_TAG_KEY }}`
(a repo-scoped deploy key with write access), so its `git push origin
<tag>` uses SSH via that key and publish.yml fires normally.

This file exists to catch the regression where someone removes the
`ssh-key` input or accidentally swaps back to GITHUB_TOKEN - both of
which leave the release pipeline silently broken (auto-tag lands, no
publish).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW.is_file(), f"missing {WORKFLOW.relative_to(REPO)}"
    return yaml.safe_load(WORKFLOW.read_text())


def _tag_job(workflow: dict) -> dict:
    jobs = workflow.get("jobs") or {}
    tag = jobs.get("tag")
    assert tag, "ci.yml must have a `tag` job"
    return tag


def _checkout_step(job: dict) -> dict:
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if "actions/checkout" in str(step.get("uses", "")):
            return step
    raise AssertionError("tag job has no actions/checkout step")


def test_tag_job_checkout_uses_release_tag_ssh_key(workflow):
    """Deploy key input on checkout: tag push must not use GITHUB_TOKEN
    or publish.yml stays dormant on new tags."""
    tag = _tag_job(workflow)
    checkout = _checkout_step(tag)
    with_args = checkout.get("with") or {}
    ssh_key = with_args.get("ssh-key")
    assert ssh_key, (
        "tag job's actions/checkout must set ssh-key: ${{ secrets.RELEASE_TAG_KEY }} "
        "so that `git push origin <tag>` fires the publish workflow"
    )
    assert "RELEASE_TAG_KEY" in ssh_key, (
        f"tag job checkout ssh-key must reference secrets.RELEASE_TAG_KEY, "
        f"got {ssh_key!r}"
    )


def test_tag_job_does_not_use_default_token_on_checkout(workflow):
    """Adversarial: if someone re-adds `token: ${{ secrets.GITHUB_TOKEN }}`
    or removes ssh-key entirely, downstream publish.yml stays silent."""
    tag = _tag_job(workflow)
    checkout = _checkout_step(tag)
    with_args = checkout.get("with") or {}
    token = with_args.get("token")
    # `token` set to something explicit is fine only if it's not GITHUB_TOKEN.
    # ssh-key path takes precedence anyway, but flag the mixed-signal state.
    if token:
        assert "GITHUB_TOKEN" not in token, (
            "tag job checkout must not use GITHUB_TOKEN - "
            "publish.yml does not wake on tags pushed by GITHUB_TOKEN"
        )

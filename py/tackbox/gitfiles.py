"""Source set from git: the impure adapter over the pure source_set logic.

source_set.py stays subprocess-free (unit-testable without a repo); the
`git ls-files` shell-out that both the linter and doctor need lives here, plus
the sanitized `git check-attr` attribute-resolution seam (step 1's deliverable
to step 2) and the snapshot that rides on it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .source_set import (
    EXCLUSION_ATTRIBUTES,
    Snapshot,
    build_link_targets,
    build_snapshot,
    filter_source_set,
    parse_check_attr,
    parse_ls_files_stage,
    parse_ls_files_untracked,
)


class AttributeResolutionError(RuntimeError):
    """git check-attr failed or emitted malformed output. Its own type - never a
    wrapped CalledProcessError - so no surface (doctor especially) can degrade a
    genuine resolution failure to "no such sources"."""


def _ls_files_raw(repo_root: Path) -> tuple[bytes, bytes]:
    """`git ls-files` staged (`-s`) and untracked, as raw `-z` streams. Raises on
    git failure - callers that tolerate a missing/failed git wrap the call."""
    stage_raw = subprocess.run(
        ["git", "ls-files", "-s", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    ).stdout
    untracked_raw = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    ).stdout
    return stage_raw, untracked_raw


def collect_source_set(repo_root: Path, scope: str = ".", changed_scope=None):
    """Run `git ls-files` (staged + untracked) under repo_root and return the
    filtered (files, warnings) source set for scope. Raises on git failure -
    callers that tolerate a missing/failed git wrap the call. Attribute exclusion
    is not applied here: this is the thin wrapper for callers needing only the
    (files, warnings) shape; the exclusion lives in collect_snapshot."""
    stage_raw, untracked_raw = _ls_files_raw(repo_root)
    return filter_source_set(
        parse_ls_files_stage(stage_raw),
        parse_ls_files_untracked(untracked_raw),
        scope,
        exists=lambda p: (repo_root / p).exists(),
        is_symlink=lambda p: (repo_root / p).is_symlink(),
        changed_scope=changed_scope,
    )


def collect_link_targets(repo_root: Path) -> list[tuple[str, str]]:
    """The whole-tree Markdown link-target inventory as sorted `(kind, path)`
    pairs (source_set.build_link_targets). Always whole-tree, regardless of lint
    scope: a scoped run of a.md may link to any b.md in the repo, so every target
    must stay resolvable. Runs `git ls-files` (no `check-attr`), so the
    one-attribute-resolution-per-command invariant is untouched. Raises on git
    failure - callers surface it as an infra error."""
    stage_raw, untracked_raw = _ls_files_raw(repo_root)
    return build_link_targets(
        parse_ls_files_stage(stage_raw),
        parse_ls_files_untracked(untracked_raw),
        exists=lambda p: (repo_root / p).exists(),
        is_symlink=lambda p: (repo_root / p).is_symlink(),
    )


def resolve_attributes(
    repo_root: Path, paths: list[str], source: str | None = None
) -> dict[str, list[str]]:
    """The attribute-resolution seam (step 1 -> step 2): resolve the three honored
    exclusion attributes for explicitly given repo-relative paths, returning
    {path: [set attribute names]} for paths with at least one set. Existence on
    disk is not required - attributes match by path, so a would-be Write target
    under an excluded glob resolves excluded. `source` maps to `--source=<rev>`
    (the rev's attributes rather than the worktree's).

    Sanitized invocation, not provenance detection: GIT_ATTR_NOSYSTEM=1,
    core.attributesFile neutralized to os.devnull, GIT_ATTR_SOURCE dropped from
    the environment (a trailing `-c attr.tree=` cannot override it), and a
    trailing `-c attr.tree=` neutralizing any preconfigured attr.tree (verified
    on git 2.50.1: it would otherwise redirect reading to the committed tree,
    making an approved worktree edit silently inert). An explicit `source` still
    wins over the neutralizer. A subprocess failure or malformed output is loud
    (AttributeResolutionError)."""
    if not paths:
        return {}
    env = dict(os.environ)
    env["GIT_ATTR_NOSYSTEM"] = "1"
    env.pop("GIT_ATTR_SOURCE", None)
    argv = [
        "git",
        "-c",
        f"core.attributesFile={os.devnull}",
        "-c",
        "attr.tree=",
        "check-attr",
        "-z",
        "--stdin",
    ]
    if source is not None:
        argv.append(f"--source={source}")
    argv += list(EXCLUSION_ATTRIBUTES)
    stdin = "".join(p + "\0" for p in paths).encode("utf-8")
    completed = subprocess.run(
        argv, cwd=repo_root, input=stdin, capture_output=True, env=env
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise AttributeResolutionError(
            f"git check-attr failed (exit {completed.returncode}): {detail}"
        )
    try:
        return parse_check_attr(completed.stdout)
    except ValueError as e:
        raise AttributeResolutionError(f"malformed git check-attr output: {e}") from e


def collect_snapshot(
    repo_root: Path, scope: str = ".", changed_scope=None
) -> Snapshot:
    """One tree inventory `(included, excluded_pairs, warnings)` from one
    attribute resolution. The candidate source set is resolved once; excluded
    files leave `included` and land in `excluded_pairs`. Callers wanting a scoped
    view build this once whole-tree and narrow in Python (source_set.narrow_files)
    so no command resolves the same population twice."""
    files, warnings = collect_source_set(repo_root, scope, changed_scope)
    excluded_map = resolve_attributes(repo_root, files)
    return build_snapshot(files, excluded_map, warnings)

"""Source set from git: the impure adapter over the pure source_set logic.

source_set.py stays subprocess-free (unit-testable without a repo); the
`git ls-files` shell-out that both the linter and doctor need lives here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .source_set import (
    filter_source_set,
    parse_ls_files_stage,
    parse_ls_files_untracked,
)


def collect_source_set(repo_root: Path, scope: str = ".", changed_scope=None):
    """Run `git ls-files` (staged + untracked) under repo_root and return the
    filtered (files, warnings) source set for scope. Raises on git failure -
    callers that tolerate a missing/failed git wrap the call."""
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
    return filter_source_set(
        parse_ls_files_stage(stage_raw),
        parse_ls_files_untracked(untracked_raw),
        scope,
        exists=lambda p: (repo_root / p).exists(),
        is_symlink=lambda p: (repo_root / p).is_symlink(),
        changed_scope=changed_scope,
    )

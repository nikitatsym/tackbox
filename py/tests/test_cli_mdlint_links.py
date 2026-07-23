"""Step MD2b acceptance: cross-file Markdown link integrity end to end.

Two levels:
- `collect_link_targets` inventory composition against a real git tree (the
  gitignored / untracked / symlink / gitlink distinctions the JS rule trusts).
- the full `python -m tackbox.cli lint .` pipeline, proving a link to a
  gitignored file is a finding and - the cache-regression guard - that a warm
  cache never hides a target whose heading was renamed or which was deleted
  (mdlint is cacheable=False, so it always re-lints).
"""

from __future__ import annotations

import os

import pytest
from conftest import commit_all, git, init_repo, run_lint

from tackbox.gitfiles import collect_link_targets


# -- inventory composition (collect_link_targets over a real tree) ---------


def test_inventory_excludes_gitignored_includes_untracked_not_ignored(tmp_path):
    init_repo(tmp_path)
    (tmp_path / "a.md").write_text("# a\n")
    (tmp_path / ".gitignore").write_text("ignored.md\n")
    (tmp_path / "ignored.md").write_text("# ignored\n")
    (tmp_path / "loose.md").write_text("# untracked but not ignored\n")
    commit_all(tmp_path)  # a.md + .gitignore committed; loose.md stays untracked

    inv = {path: kind for kind, path in collect_link_targets(tmp_path)}
    assert inv.get("a.md") == "F"
    assert inv.get("loose.md") == "F"  # untracked, not ignored -> a valid target
    assert "ignored.md" not in inv  # gitignored -> absent -> a link to it is broken


def test_inventory_marks_tracked_symlink_L_and_gitlink_G(tmp_path):
    if os.name == "nt":
        pytest.skip("symlink creation is privileged on Windows")
    init_repo(tmp_path)
    (tmp_path / "real.md").write_text("# real\n")
    (tmp_path / "link.md").symlink_to("real.md")
    # A committed nested repo becomes a gitlink (mode 160000) in the parent index.
    sub = tmp_path / "vendor" / "sub"
    sub.mkdir(parents=True)
    init_repo(sub)
    (sub / "doc.md").write_text("# sub doc\n")
    commit_all(sub)
    git(tmp_path, "add", "real.md", "link.md", "vendor/sub")
    git(tmp_path, "commit", "-q", "-m", "with symlink and gitlink")

    inv = {path: kind for kind, path in collect_link_targets(tmp_path)}
    assert inv.get("real.md") == "F"
    assert inv.get("link.md") == "L"
    assert inv.get("vendor/sub") == "G"


# -- full pipeline: gitignored target is a finding -------------------------


def test_lint_flags_link_to_gitignored_target(tmp_path):
    init_repo(tmp_path)
    (tmp_path / "a.md").write_text("# a\n\n[x](secret.md)\n")
    (tmp_path / ".gitignore").write_text("secret.md\n")
    (tmp_path / "secret.md").write_text("# secret\n")
    commit_all(tmp_path)

    r = run_lint(tmp_path, tmp_path / "cache")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "MD-LINK" in r.stdout
    assert "secret.md" in r.stdout


# -- cache-regression: a warm cache never hides a broken target ------------


def test_warm_cache_still_catches_renamed_target_heading(tmp_path):
    init_repo(tmp_path)
    (tmp_path / "a.md").write_text("# a\n\n[jump](b.md#target-heading)\n")
    (tmp_path / "b.md").write_text("# Target Heading\n")
    commit_all(tmp_path)
    cache_home = tmp_path / "cache"

    warm = run_lint(tmp_path, cache_home)
    assert warm.returncode == 0, warm.stdout + warm.stderr

    # a.md is untouched; only the target heading changes. A per-file mdlint cache
    # would call a.md clean and miss this - cacheable=False forces the re-lint.
    (tmp_path / "b.md").write_text("# Renamed Heading\n")
    commit_all(tmp_path, "rename heading")

    second = run_lint(tmp_path, cache_home)
    assert second.returncode == 1, second.stdout + second.stderr
    assert "fragment not found" in second.stdout


def test_warm_cache_still_catches_deleted_target(tmp_path):
    init_repo(tmp_path)
    (tmp_path / "a.md").write_text("# a\n\n[x](b.md)\n")
    (tmp_path / "b.md").write_text("# b\n")
    commit_all(tmp_path)
    cache_home = tmp_path / "cache"

    assert run_lint(tmp_path, cache_home).returncode == 0

    (tmp_path / "b.md").unlink()
    git(tmp_path, "rm", "-q", "b.md")
    commit_all(tmp_path, "drop target")

    second = run_lint(tmp_path, cache_home)
    assert second.returncode == 1, second.stdout + second.stderr
    assert "does not exist" in second.stdout

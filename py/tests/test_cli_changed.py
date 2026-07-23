"""Step 7 acceptance: --changed / --since=<ref> scope flags.

Semantics per plan (Roadmap item 3, step 7):

- `--changed` alone: dirty tree (staged + unstaged + untracked).
- `--since=<ref>`: three-dot diff `<ref>...HEAD`, unioned with dirty tree.
- `--changed --since=<ref>` is equivalent to `--since=<ref>` (superset).
- Positional `path` intersects with these flags - orthogonal filters compose.
- `.go` files expand to their containing package via package_mode dispatch.

The three-dot vs two-dot test is adversarial: it plants a violation on the
reference branch AFTER the fork and confirms it does NOT appear via
`--since=<ref>`. A two-dot implementation would leak that noise.

Fixture files are materialized in tmp git repos - tackbox self-lint never
sees them.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import commit_all, git, init_repo, tackbox_env


MD_CLEAN = "# Notes\n\nAll ASCII here.\n"
# A chars=ascii carrier declares the check; the U+2014 em-dash then triggers
# MD-CHARS / declared-chars.
MD_VIOLATE = "<!-- tackbox: chars=ascii -->\n# Notes\n\nSome text \u2014 with em-dash.\n"

GO_MOD = "module changedfixture\n\ngo 1.24\n"

GO_ERC001 = """package pkg

import "errors"

func Fail() error {
\terr := errors.New("boom")
\tif err != nil {
\t\t_ = "swallowed"
\t}
\treturn errors.New("noop")
}
"""

GO_CLEAN = """package pkg

func Two() int {
\treturn 2
}
"""


def _needs_node():
    if shutil.which("node") is None:
        pytest.fail("node not installed; install it, do not skip")


def _needs_go():
    if shutil.which("go") is None:
        pytest.fail("go toolchain not installed; install it, do not skip")


def _run_tackbox(
    repo: Path, *flags: str, path: str = "."
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "lint", path, "--no-cache", *flags],
        cwd=repo,
        env=tackbox_env(),
        capture_output=True,
        text=True,
    )


# -- --changed with an empty dirty tree ------------------------------------


def test_changed_on_clean_repo_reports_empty_scope(tmp_path):
    """No staged, unstaged, or untracked -> scope is empty -> exit 2."""
    _needs_node()
    (tmp_path / "notes.md").write_text(MD_CLEAN)
    init_repo(tmp_path)
    commit_all(tmp_path)

    r = _run_tackbox(tmp_path, "--changed")
    assert r.returncode == 2, (
        f"expected 2 (no dirty files), got {r.returncode}\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    assert "matched no files" in r.stderr


# -- --changed picks up each dirty-tree category --------------------------


def test_changed_picks_up_staged_file(tmp_path):
    _needs_node()
    (tmp_path / "notes.md").write_text(MD_CLEAN)
    init_repo(tmp_path)
    commit_all(tmp_path)

    (tmp_path / "notes.md").write_text(MD_VIOLATE)
    git(tmp_path, "add", "notes.md")

    r = _run_tackbox(tmp_path, "--changed")
    assert r.returncode == 1, (
        f"staged violation must fail --changed run\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    assert "notes.md" in r.stdout


def test_changed_picks_up_unstaged_file(tmp_path):
    _needs_node()
    (tmp_path / "notes.md").write_text(MD_CLEAN)
    init_repo(tmp_path)
    commit_all(tmp_path)

    (tmp_path / "notes.md").write_text(MD_VIOLATE)
    # Do not `git add`; violation lives only in the worktree.

    r = _run_tackbox(tmp_path, "--changed")
    assert r.returncode == 1, (
        f"unstaged violation must fail --changed run\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    assert "notes.md" in r.stdout


def test_changed_picks_up_untracked_file(tmp_path):
    _needs_node()
    (tmp_path / "notes.md").write_text(MD_CLEAN)
    init_repo(tmp_path)
    commit_all(tmp_path)

    (tmp_path / "extra.md").write_text(MD_VIOLATE)
    # extra.md is not tracked.

    r = _run_tackbox(tmp_path, "--changed")
    assert r.returncode == 1, (
        f"untracked violation must fail --changed run\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    assert "extra.md" in r.stdout


def test_changed_excludes_committed_but_unmodified_files(tmp_path):
    """Adversarial: a violation that was committed but never touched again
    must NOT surface via --changed - the whole point of the flag is scope
    narrowing."""
    _needs_node()
    (tmp_path / "dirty.md").write_text(MD_CLEAN)
    (tmp_path / "old-violation.md").write_text(MD_VIOLATE)
    init_repo(tmp_path)
    commit_all(tmp_path)

    # Modify only dirty.md (still clean content); old-violation.md is not
    # touched in the worktree.
    (tmp_path / "dirty.md").write_text(MD_CLEAN + "\nMore ASCII.\n")

    r = _run_tackbox(tmp_path, "--changed")
    assert r.returncode == 0, (
        f"unmodified old violations must not be linted under --changed\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    assert "old-violation.md" not in r.stdout


# -- --changed intersects with positional path ----------------------------


def test_changed_intersects_with_path_prefix(tmp_path):
    """`tackbox lint <path> --changed` filters the dirty tree by directory
    boundary (plan: path and --changed are orthogonal filters)."""
    _needs_node()
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "notes.md").write_text(MD_CLEAN)
    (tmp_path / "docs" / "notes.md").write_text(MD_CLEAN)
    init_repo(tmp_path)
    commit_all(tmp_path)

    # Both files are dirty with violations.
    (tmp_path / "src" / "notes.md").write_text(MD_VIOLATE)
    (tmp_path / "docs" / "notes.md").write_text(MD_VIOLATE)

    r = _run_tackbox(tmp_path, "--changed", path="docs")
    assert r.returncode == 1
    # docs/ is in scope.
    assert "docs/notes.md" in r.stdout
    # src/ is filtered out by the positional path.
    assert "src/notes.md" not in r.stdout


# -- --changed with .go: package expansion --------------------------------


def test_changed_go_file_expands_to_containing_package(tmp_path):
    """A dirty .go file scopes erclint to its package; a sibling file in the
    same package with a violation gets linted through package expansion."""
    _needs_go()
    _needs_node()
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "clean.go").write_text(GO_CLEAN)
    (tmp_path / "pkg" / "violate.go").write_text(GO_ERC001)
    init_repo(tmp_path)
    commit_all(tmp_path)

    # Only clean.go is modified; violate.go is untouched but shares the
    # package. Plan step 7 acceptance: erclint expands to the whole package.
    (tmp_path / "pkg" / "clean.go").write_text(GO_CLEAN + "\nvar _ = 1\n")

    r = _run_tackbox(tmp_path, "--changed")
    assert r.returncode == 1, (
        f"expected 1 (ERC001 in package sibling), got {r.returncode}\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    assert "ERC001" in r.stdout
    assert "violate.go" in r.stdout


# -- --since=<ref>: three-dot semantics -----------------------------------


def _setup_forked_repo(tmp_path: Path) -> Path:
    """Repo with a feature branch forked from main.

    Layout after setup (HEAD is on the feature branch):

    - Commit A on main: feature.md CLEAN, main-only.md CLEAN, unrelated.md CLEAN
    - Commit F on feature: feature.md MODIFIED (still ASCII)
    - Commit M on main: main-only.md MODIFIED with a violation
    - HEAD checked out to feature.

    merge-base(main, HEAD) == A. `git diff main...HEAD` = {feature.md}.
    `git diff main..HEAD`  = {feature.md, main-only.md}.

    Three-dot must exclude main-only.md; a two-dot implementation would
    include it and its violation would show up.
    """
    (tmp_path / "feature.md").write_text(MD_CLEAN)
    (tmp_path / "main-only.md").write_text(MD_CLEAN)
    (tmp_path / "unrelated.md").write_text(MD_CLEAN)
    init_repo(tmp_path)
    commit_all(tmp_path, "base A")

    git(tmp_path, "checkout", "-q", "-b", "feature")
    (tmp_path / "feature.md").write_text(MD_CLEAN + "\nSecond line.\n")
    commit_all(tmp_path, "feature edit F")

    git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "main-only.md").write_text(MD_VIOLATE)
    commit_all(tmp_path, "main violation M")

    git(tmp_path, "checkout", "-q", "feature")
    return tmp_path


def test_since_ref_three_dot_ignores_ref_progression_after_fork(tmp_path):
    """Adversarial: main progressed after fork with a violation. A two-dot
    diff would leak main-only.md into --since scope; three-dot must not."""
    _needs_node()
    repo = _setup_forked_repo(tmp_path)

    r = _run_tackbox(repo, "--since=main")
    assert r.returncode == 0, (
        f"three-dot must not include main-only.md violation\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    # feature.md is in scope but clean; main-only.md must not appear anywhere.
    assert "main-only.md" not in r.stdout
    assert "unrelated.md" not in r.stdout


def test_since_ref_picks_up_files_changed_on_the_feature_branch(tmp_path):
    """Positive: modify feature.md with a violation on the branch; --since=main
    must scope to it and surface the violation."""
    _needs_node()
    (tmp_path / "feature.md").write_text(MD_CLEAN)
    (tmp_path / "unrelated.md").write_text(MD_CLEAN)
    init_repo(tmp_path)
    commit_all(tmp_path, "base")

    git(tmp_path, "checkout", "-q", "-b", "feature")
    (tmp_path / "feature.md").write_text(MD_VIOLATE)
    commit_all(tmp_path, "feature violation")

    r = _run_tackbox(tmp_path, "--since=main")
    assert r.returncode == 1, (
        f"expected 1 (feature.md em-dash), got {r.returncode}\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    assert "feature.md" in r.stdout
    assert "unrelated.md" not in r.stdout


def test_since_ref_includes_dirty_tree_union_with_diff(tmp_path):
    """`--since=<ref>` scope includes dirty tree even without any commits on
    the branch - plan: 'иначе --since=main пропустит несохранённые правки'."""
    _needs_node()
    (tmp_path / "committed.md").write_text(MD_CLEAN)
    init_repo(tmp_path)
    commit_all(tmp_path)

    git(tmp_path, "checkout", "-q", "-b", "feature")
    # No commits on feature. Just an untracked file with a violation.
    (tmp_path / "dirty.md").write_text(MD_VIOLATE)

    r = _run_tackbox(tmp_path, "--since=main")
    assert r.returncode == 1, (
        f"--since must include dirty tree, got {r.returncode}\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    assert "dirty.md" in r.stdout


def test_changed_and_since_together_equals_since_alone(tmp_path):
    """`--changed --since=<ref>` is a synonym for `--since=<ref>` (superset).

    Compare sorted stdout lines: mdlint's async pipeline can emit findings
    in completion order, but the set of findings must be identical."""
    _needs_node()
    (tmp_path / "committed.md").write_text(MD_CLEAN)
    init_repo(tmp_path)
    commit_all(tmp_path, "base")

    git(tmp_path, "checkout", "-q", "-b", "feature")
    (tmp_path / "committed.md").write_text(MD_VIOLATE)
    commit_all(tmp_path, "branch violation")

    # Also add an untracked dirty file.
    (tmp_path / "extra.md").write_text(MD_VIOLATE)

    only_since = _run_tackbox(tmp_path, "--since=main")
    both = _run_tackbox(tmp_path, "--changed", "--since=main")

    assert only_since.returncode == both.returncode
    assert sorted(only_since.stdout.splitlines()) == sorted(both.stdout.splitlines())


# -- Flag parsing ---------------------------------------------------------


def test_since_requires_a_value(tmp_path):
    """`--since` without a value must fail argparse - the ref is required."""
    _needs_node()
    (tmp_path / "notes.md").write_text(MD_CLEAN)
    init_repo(tmp_path)
    commit_all(tmp_path)

    r = _run_tackbox(tmp_path, "--since")
    assert r.returncode == 2, (
        f"expected argparse error (2), got {r.returncode}\n"
        f"stderr={r.stderr!r}"
    )


def test_since_unknown_ref_fails_loudly(tmp_path):
    """An unresolved ref must fail loud, not silently degrade to full-scan."""
    _needs_node()
    (tmp_path / "notes.md").write_text(MD_CLEAN)
    init_repo(tmp_path)
    commit_all(tmp_path)

    r = _run_tackbox(tmp_path, "--since=does-not-exist")
    assert r.returncode != 0, (
        f"unknown ref must not succeed silently\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )


def test_changed_on_fresh_repo_without_commits_fails_cleanly(tmp_path):
    """`git init` with no commit: HEAD does not resolve. Onboarding case -
    must surface a tackbox message, not a Python traceback."""
    init_repo(tmp_path)
    (tmp_path / "notes.md").write_text(MD_CLEAN)

    r = _run_tackbox(tmp_path, "--changed")
    assert r.returncode == 2, (
        f"expected 2, got {r.returncode}\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    assert "requires at least one commit" in r.stderr
    assert "Traceback" not in r.stderr

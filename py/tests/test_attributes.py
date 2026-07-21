"""The attribute-resolution seam and the snapshot (gitfiles), against real git
repos. Covers the step-1 source_set/gitfiles fixture list of the
generated-code-attributes plan.

Each `.gitattributes` case is generated inline into a tmp repo (never a tracked
fixture) so tackbox self-lint never scans it. The resolution reads the worktree
through a sanitized `git check-attr`; the fixtures pin exclusion semantics,
sanitization, the not-on-disk / source-revision seam contract, and that
malformed / failed resolution is loud.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import commit_all, count_calls, git, init_repo

from tackbox import gitfiles
from tackbox.gitfiles import (
    AttributeResolutionError,
    collect_snapshot,
    resolve_attributes,
)
from tackbox.source_set import narrow_files


def _needs_git():
    if not shutil.which("git"):
        pytest.fail("`git` toolchain not found on PATH; install it, do not skip")


def _repo(tmp_path: Path, attributes: str, files: dict[str, str]) -> Path:
    """A committed repo with a root `.gitattributes` plus `files`."""
    _needs_git()
    (tmp_path / ".gitattributes").write_text(attributes, encoding="utf-8")
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    init_repo(tmp_path, commit=True)
    return tmp_path


# -- resolve_attributes: exclusion semantics ------------------------------


def test_marked_tracked_file_excluded(tmp_path):
    root = _repo(tmp_path, "gen/*.pb.go linguist-generated\n", {"gen/api.pb.go": "package gen\n"})
    assert resolve_attributes(root, ["gen/api.pb.go"]) == {
        "gen/api.pb.go": ["linguist-generated"]
    }


def test_marked_untracked_file_excluded(tmp_path):
    root = _repo(tmp_path, "gen/*.pb.go linguist-generated\n", {"gen/api.pb.go": "package gen\n"})
    # Untracked file matching the glob resolves the same way (attributes match by
    # path, not index).
    (root / "gen" / "new.pb.go").write_text("package gen\n")
    assert resolve_attributes(root, ["gen/new.pb.go"]) == {
        "gen/new.pb.go": ["linguist-generated"]
    }


def test_subdir_gitattributes_governs_subtree(tmp_path):
    _needs_git()
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / ".gitattributes").write_text("** linguist-vendored\n")
    (tmp_path / "vendor" / "lib.go").write_text("package vendor\n")
    (tmp_path / "app.go").write_text("package app\n")
    init_repo(tmp_path, commit=True)
    resolved = resolve_attributes(tmp_path, ["vendor/lib.go", "app.go"])
    assert resolved == {"vendor/lib.go": ["linguist-vendored"]}


def test_explicit_true_excluded(tmp_path):
    root = _repo(tmp_path, "x.go linguist-generated=true\n", {"x.go": "package p\n"})
    assert resolve_attributes(root, ["x.go"]) == {"x.go": ["linguist-generated"]}


def test_explicit_false_reincludes(tmp_path):
    root = _repo(
        tmp_path,
        "gen/** linguist-generated\ngen/keep.go linguist-generated=false\n",
        {"gen/api.pb.go": "package gen\n", "gen/keep.go": "package gen\n"},
    )
    resolved = resolve_attributes(root, ["gen/api.pb.go", "gen/keep.go"])
    assert resolved == {"gen/api.pb.go": ["linguist-generated"]}


def test_dash_attr_unset_form_kept_in(tmp_path):
    root = _repo(tmp_path, "x.go -linguist-generated\n", {"x.go": "package p\n"})
    assert resolve_attributes(root, ["x.go"]) == {}


def test_bang_attr_form_kept_in(tmp_path):
    root = _repo(tmp_path, "x.go !linguist-generated\n", {"x.go": "package p\n"})
    assert resolve_attributes(root, ["x.go"]) == {}


def test_unspecified_default_kept_in(tmp_path):
    root = _repo(tmp_path, "", {"x.go": "package p\n"})
    assert resolve_attributes(root, ["x.go"]) == {}


def test_non_honored_attribute_kept_in(tmp_path):
    # linguist-documentation is set but not honored: the file stays in.
    root = _repo(tmp_path, "docs.go linguist-documentation\n", {"docs.go": "package p\n"})
    assert resolve_attributes(root, ["docs.go"]) == {}


def test_multiple_attributes_one_pair_per_attr_deterministic(tmp_path):
    root = _repo(
        tmp_path,
        "both.go linguist-generated gitlab-generated\n",
        {"both.go": "package p\n"},
    )
    assert resolve_attributes(root, ["both.go"]) == {
        "both.go": ["gitlab-generated", "linguist-generated"]
    }


def test_gitlab_generated_and_vendored_spellings(tmp_path):
    root = _repo(
        tmp_path,
        "g.go gitlab-generated\nv.go linguist-vendored\n",
        {"g.go": "package p\n", "v.go": "package p\n"},
    )
    assert resolve_attributes(root, ["g.go", "v.go"]) == {
        "g.go": ["gitlab-generated"],
        "v.go": ["linguist-vendored"],
    }


def test_stdin_batching_over_large_list(tmp_path):
    # Paths ride stdin (ARG_MAX-safe), never argv: a list far past a single
    # command line resolves in one call.
    _needs_git()
    lines = "".join(f"gen/f{i}.pb.go\n" for i in range(2000))
    (tmp_path / ".gitattributes").write_text("gen/*.pb.go linguist-generated\n")
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "f0.pb.go").write_text("package gen\n")
    (tmp_path / "keep.go").write_text("package p\n")
    init_repo(tmp_path, commit=True)
    paths = [f"gen/f{i}.pb.go" for i in range(2000)] + ["keep.go"]
    resolved = resolve_attributes(tmp_path, paths)
    assert len(resolved) == 2000
    assert "keep.go" not in resolved
    assert resolved["gen/f1999.pb.go"] == ["linguist-generated"]
    # (list-file / stdin size sanity: the joined stdin exceeds a bare argv line)
    assert len(lines) > 4096


# -- resolve_attributes: sanitized invocation -----------------------------


def test_global_attributes_file_ignored(tmp_path, monkeypatch):
    # A user global core.attributesFile must not leak into resolution.
    _needs_git()
    glob = tmp_path / "global_attrs"
    glob.write_text("plain.go linguist-generated\n")
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "plain.go").write_text("package p\n")
    init_repo(tmp_path / "repo", commit=True)
    # Point the process env's HOME + a repo-level config at the global file; the
    # sanitized invocation neutralizes core.attributesFile to os.devnull.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gc"))
    (tmp_path / "gc").write_text(f"[core]\n\tattributesFile = {glob}\n")
    # Sanity: without sanitization git would resolve plain.go as set.
    raw = subprocess.run(
        ["git", "check-attr", "-z", "--stdin", "linguist-generated"],
        cwd=tmp_path / "repo", input=b"plain.go\0", capture_output=True,
    ).stdout
    assert b"set" in raw, "global attrs file did not take effect (test setup bug)"
    assert resolve_attributes(tmp_path / "repo", ["plain.go"]) == {}


def test_git_attr_source_env_neutralized(tmp_path, monkeypatch):
    # A worktree edit that adds an exclusion not yet committed must resolve as
    # set; a stale GIT_ATTR_SOURCE=HEAD would make it inert (read the commit).
    root = _repo(tmp_path, "committed.go linguist-generated\n", {"committed.go": "package p\n"})
    (root / ".gitattributes").write_text(
        "committed.go linguist-generated\nlate.go linguist-generated\n"
    )
    (root / "late.go").write_text("package p\n")
    monkeypatch.setenv("GIT_ATTR_SOURCE", "HEAD")
    resolved = resolve_attributes(root, ["late.go"])
    assert resolved == {"late.go": ["linguist-generated"]}, (
        "GIT_ATTR_SOURCE=HEAD was not dropped - worktree edit went inert"
    )


def test_attr_tree_config_neutralized(tmp_path):
    # A repo-level attr.tree=HEAD would redirect reading to the committed tree;
    # the trailing `-c attr.tree=` must neutralize it so the worktree edit counts.
    root = _repo(tmp_path, "committed.go linguist-generated\n", {"committed.go": "package p\n"})
    git(root, "config", "attr.tree", "HEAD")
    (root / ".gitattributes").write_text(
        "committed.go linguist-generated\nlate.go linguist-generated\n"
    )
    (root / "late.go").write_text("package p\n")
    resolved = resolve_attributes(root, ["late.go"])
    assert resolved == {"late.go": ["linguist-generated"]}


# -- resolve_attributes: not-on-disk + source revision (the seam contract) -


def test_resolves_path_not_on_disk(tmp_path):
    # A would-be Write target under an excluded glob resolves excluded without
    # existing - step 2's excluded-target Pre arm depends on this.
    root = _repo(tmp_path, "gen/*.pb.go linguist-generated\n", {"gen/api.pb.go": "package gen\n"})
    assert not (root / "gen" / "brandnew.pb.go").exists()
    assert resolve_attributes(root, ["gen/brandnew.pb.go"]) == {
        "gen/brandnew.pb.go": ["linguist-generated"]
    }


def test_source_revision_reports_rev_attributes_not_worktree(tmp_path):
    # source=<rev> reads the rev's attributes; a worktree-only addition is absent
    # there, a committed one is present.
    root = _repo(tmp_path, "committed.go linguist-generated\n", {"committed.go": "package p\n"})
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    ).stdout.strip()
    # Add a worktree-only exclusion after the commit.
    (root / ".gitattributes").write_text(
        "committed.go linguist-generated\nlate.go linguist-generated\n"
    )
    (root / "late.go").write_text("package p\n")
    at_rev = resolve_attributes(root, ["committed.go", "late.go"], source=head)
    assert at_rev == {"committed.go": ["linguist-generated"]}  # late.go not in the rev
    at_worktree = resolve_attributes(root, ["committed.go", "late.go"])
    assert at_worktree == {
        "committed.go": ["linguist-generated"],
        "late.go": ["linguist-generated"],
    }


def test_empty_paths_no_subprocess(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("check-attr must not run for an empty path list")

    monkeypatch.setattr(gitfiles.subprocess, "run", boom)
    assert resolve_attributes(tmp_path, []) == {}


# -- adversarial: resolution failure / malformed output is loud -----------


def test_malformed_check_attr_output_is_infra_error(tmp_path, monkeypatch):
    # Attack the guarantee: a git that emits a truncated -z stream must be loud,
    # never a silent "nothing excluded".
    _needs_git()
    root = _repo(tmp_path, "gen/*.pb.go linguist-generated\n", {"gen/api.pb.go": "package gen\n"})

    class _Truncated:
        returncode = 0
        stdout = b"gen/api.pb.go\0linguist-generated\0"  # missing the value field
        stderr = b""

    monkeypatch.setattr(gitfiles.subprocess, "run", lambda *a, **k: _Truncated())
    with pytest.raises(AttributeResolutionError):
        resolve_attributes(root, ["gen/api.pb.go"])


def test_check_attr_subprocess_failure_is_loud(tmp_path):
    # A bad source rev fails the subprocess; it travels as AttributeResolutionError,
    # not a wrapped CalledProcessError.
    root = _repo(tmp_path, "x.go linguist-generated\n", {"x.go": "package p\n"})
    with pytest.raises(AttributeResolutionError):
        resolve_attributes(root, ["x.go"], source="no-such-rev")


def test_resolution_error_is_not_called_process_error(tmp_path):
    # doctor must not swallow it via `except CalledProcessError`.
    assert not issubclass(AttributeResolutionError, subprocess.CalledProcessError)


# -- collect_snapshot ------------------------------------------------------


def test_snapshot_returns_included_excluded_pairs_and_warnings(tmp_path):
    root = _repo(
        tmp_path,
        "gen/*.pb.go linguist-generated\nboth.go linguist-generated gitlab-generated\n",
        {"gen/api.pb.go": "package gen\n", "both.go": "package p\n", "app.go": "package p\n"},
    )
    # A tracked file removed from the worktree makes a warning.
    (root / "app.go").unlink()
    snap = collect_snapshot(root)
    assert "gen/api.pb.go" not in snap.included
    assert "both.go" not in snap.included
    assert ".gitattributes" in snap.included
    assert snap.excluded_pairs == [
        ("both.go", "gitlab-generated"),
        ("both.go", "linguist-generated"),
        ("gen/api.pb.go", "linguist-generated"),
    ]
    assert snap.excluded_files == frozenset({"both.go", "gen/api.pb.go"})
    assert any(w.path == "app.go" for w in snap.warnings)


def test_snapshot_one_resolution_reused_for_scopes(tmp_path, monkeypatch):
    # collect_snapshot resolves attributes exactly once; scoped views narrow it
    # in Python without re-resolving.
    root = _repo(
        tmp_path,
        "gen/*.pb.go linguist-generated\n",
        {"gen/api.pb.go": "package gen\n", "src/app.go": "package app\n"},
    )
    calls = count_calls(monkeypatch, gitfiles, "resolve_attributes")
    snap = collect_snapshot(root)
    assert calls["n"] == 1
    assert narrow_files(snap.candidate_files(), "src") == ["src/app.go"]
    assert calls["n"] == 1

"""Semantic spec for tackbox.source_set.

Each test names one case from the plan's step 2 fixture list. Positive
cases pin behaviour; negative cases (`_rejected` suffix) pin refusals.
"""

from __future__ import annotations

import pytest

from tackbox.source_set import (
    GITLINK_MODE,
    SYMLINK_MODE,
    IndexEntry,
    PathspecMagicError,
    SourceWarning,
    files_to_go_packages,
    filter_source_set,
    narrow_by_path,
    parse_ls_files_stage,
    parse_ls_files_untracked,
    validate_path,
)


def _row(mode: int, path: str, sha: str = "0" * 40) -> bytes:
    return f"{mode:o} {sha} 0\t{path}\0".encode("utf-8")


# -- parse_ls_files_stage --------------------------------------------------


def test_parse_stage_empty():
    assert parse_ls_files_stage(b"") == []


def test_parse_stage_regular_file():
    assert parse_ls_files_stage(_row(0o100644, "foo.go")) == [
        IndexEntry(path="foo.go", mode=0o100644),
    ]


def test_parse_stage_executable_file():
    assert parse_ls_files_stage(_row(0o100755, "run.sh")) == [
        IndexEntry(path="run.sh", mode=0o100755),
    ]


def test_parse_stage_gitlink():
    assert parse_ls_files_stage(_row(0o160000, "vendor/sub")) == [
        IndexEntry(path="vendor/sub", mode=GITLINK_MODE),
    ]


def test_parse_stage_symlink():
    assert parse_ls_files_stage(_row(0o120000, "link")) == [
        IndexEntry(path="link", mode=SYMLINK_MODE),
    ]


def test_parse_stage_multiple_rows_and_paths_with_spaces():
    raw = _row(0o100644, "a.go") + _row(0o100644, "dir with space/b.go")
    assert parse_ls_files_stage(raw) == [
        IndexEntry(path="a.go", mode=0o100644),
        IndexEntry(path="dir with space/b.go", mode=0o100644),
    ]


def test_parse_stage_utf8_path():
    raw = _row(0o100644, "docs/план.md")
    assert parse_ls_files_stage(raw) == [
        IndexEntry(path="docs/план.md", mode=0o100644),
    ]


def test_parse_stage_malformed_row_rejected():
    with pytest.raises(ValueError):
        parse_ls_files_stage(b"garbage-no-tab\0")


# -- parse_ls_files_untracked ---------------------------------------------


def test_parse_untracked_empty():
    assert parse_ls_files_untracked(b"") == []


def test_parse_untracked_multiple():
    assert parse_ls_files_untracked(b"a.txt\0b.txt\0") == ["a.txt", "b.txt"]


def test_parse_untracked_utf8():
    raw = "план.md\0".encode("utf-8")
    assert parse_ls_files_untracked(raw) == ["план.md"]


# -- validate_path ---------------------------------------------------------


def test_validate_dot_ok():
    validate_path(".")


def test_validate_regular_ok():
    validate_path("src/foo/bar.go")


def test_validate_nested_subtree_ok():
    validate_path("go/cmd/erclint")


def test_validate_empty_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path("")


def test_validate_glob_star_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path("*.go")


def test_validate_glob_question_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path("foo?.go")


def test_validate_glob_bracket_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path("[abc].go")


def test_validate_bang_prefix_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path("!foo")


def test_validate_pathspec_exclude_magic_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path(":(exclude)foo")


def test_validate_pathspec_short_magic_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path(":!foo")


def test_validate_absolute_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path("/etc/passwd")


def test_validate_parent_traversal_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path("../secret")


def test_validate_parent_traversal_nested_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path("src/../../secret")


# -- narrow_by_path --------------------------------------------------------


def test_narrow_dot_returns_all():
    paths = ["a.go", "src/b.go", "src/nested/c.go"]
    assert narrow_by_path(paths, ".") == paths


def test_narrow_exact_file():
    paths = ["a.go", "src/b.go", "src/nested/c.go"]
    assert narrow_by_path(paths, "src/b.go") == ["src/b.go"]


def test_narrow_subtree():
    paths = ["a.go", "src/b.go", "src/nested/c.go", "other/d.go"]
    assert narrow_by_path(paths, "src") == ["src/b.go", "src/nested/c.go"]


def test_narrow_prefix_trap_not_matched():
    # src/foo must NOT swallow src/foobar - directory-boundary only.
    paths = ["src/foo/inner.go", "src/foobar/inner.go"]
    assert narrow_by_path(paths, "src/foo") == ["src/foo/inner.go"]


def test_narrow_exact_directory_and_file_of_same_prefix():
    paths = ["src/foo", "src/foo/inner.go", "src/foobar/inner.go"]
    assert narrow_by_path(paths, "src/foo") == ["src/foo", "src/foo/inner.go"]


def test_narrow_scope_with_trailing_slash_treated_as_dir():
    paths = ["src/foo/a.go", "src/foobar/b.go"]
    assert narrow_by_path(paths, "src/foo/") == ["src/foo/a.go"]


# -- filter_source_set -----------------------------------------------------


def _always(_path: str) -> bool:
    return True


def _never(_path: str) -> bool:
    return False


def _in(members):
    frozen = frozenset(members)
    return lambda p: p in frozen


def test_filter_tracked_and_untracked_combined_sorted():
    stage = [IndexEntry("a.go", 0o100644)]
    untracked = ["b.go"]
    files, warnings = filter_source_set(stage, untracked, ".", _always, _never)
    assert files == ["a.go", "b.go"]
    assert warnings == []


def test_filter_gitlink_dropped():
    stage = [
        IndexEntry("a.go", 0o100644),
        IndexEntry("vendor/sub", GITLINK_MODE),
    ]
    files, warnings = filter_source_set(stage, [], ".", _always, _never)
    assert files == ["a.go"]
    assert warnings == []


def test_filter_tracked_symlink_dropped():
    stage = [
        IndexEntry("a.go", 0o100644),
        IndexEntry("link", SYMLINK_MODE),
    ]
    files, warnings = filter_source_set(stage, [], ".", _always, _never)
    assert files == ["a.go"]


def test_filter_untracked_symlink_dropped():
    files, warnings = filter_source_set(
        [], ["a.go", "link"], ".", _always, _in({"link"})
    )
    assert files == ["a.go"]


def test_filter_deleted_from_worktree_emits_warning_and_skips():
    stage = [
        IndexEntry("a.go", 0o100644),
        IndexEntry("gone.go", 0o100644),
    ]
    files, warnings = filter_source_set(
        stage, [], ".", _in({"a.go"}), _never
    )
    assert files == ["a.go"]
    assert warnings == [
        SourceWarning(path="gone.go", reason="tracked file missing from worktree"),
    ]


def test_filter_narrow_by_scope_applied_after_edge_case_pruning():
    stage = [
        IndexEntry("src/a.go", 0o100644),
        IndexEntry("src/link", SYMLINK_MODE),
        IndexEntry("other/b.go", 0o100644),
    ]
    files, warnings = filter_source_set(stage, [], "src", _always, _never)
    assert files == ["src/a.go"]
    assert warnings == []


def test_filter_dedup_when_untracked_shadows_tracked():
    # Not a real git state, but the filter is defensive: sorted+unique.
    stage = [IndexEntry("a.go", 0o100644)]
    files, _ = filter_source_set(stage, ["a.go"], ".", _always, _never)
    assert files == ["a.go"]


def test_filter_rejects_pathspec_magic_scope():
    with pytest.raises(PathspecMagicError):
        filter_source_set([], [], "*.go", _always, _never)


# -- files_to_go_packages --------------------------------------------------


def test_pkgs_empty():
    assert files_to_go_packages([]) == []


def test_pkgs_no_go_files():
    assert files_to_go_packages(["a.js", "b.md"]) == []


def test_pkgs_root_go_file_maps_to_dot():
    assert files_to_go_packages(["main.go"]) == ["."]


def test_pkgs_single_package():
    assert files_to_go_packages(["pkg/a.go", "pkg/b.go"]) == ["pkg"]


def test_pkgs_multiple_sorted_unique():
    assert files_to_go_packages(
        ["z/z.go", "a/a.go", "m/m.go", "a/b.go"]
    ) == ["a", "m", "z"]


def test_pkgs_non_go_files_ignored():
    assert files_to_go_packages(
        ["pkg/a.go", "pkg/README.md", "pkg/b.js", "other/c.txt"]
    ) == ["pkg"]


def test_pkgs_test_files_included_as_go():
    assert files_to_go_packages(["pkg/foo_test.go"]) == ["pkg"]

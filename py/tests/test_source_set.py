"""Semantic spec for tackbox.source_set.

Each test names one case from the plan's step 2 fixture list. Positive
cases pin behaviour; negative cases (`_rejected` suffix) pin refusals.
"""

from __future__ import annotations

import pytest

from tackbox.source_set import (
    EXCLUSION_ATTRIBUTES,
    GITLINK_MODE,
    SYMLINK_MODE,
    IndexEntry,
    PathspecMagicError,
    Snapshot,
    SourceWarning,
    build_link_targets,
    build_snapshot,
    files_to_go_packages,
    filter_source_set,
    group_go_packages_by_module,
    narrow_by_path,
    narrow_files,
    parse_check_attr,
    parse_git_diff_names,
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


def test_parse_stage_missing_stage_field_rejected():
    # Two fields (mode + sha) instead of three - reject before decoding.
    with pytest.raises(ValueError):
        parse_ls_files_stage(b"100644 " + b"0" * 40 + b"\tfoo\0")


def test_parse_stage_extra_header_field_rejected():
    # Four fields in the header - reject.
    with pytest.raises(ValueError):
        parse_ls_files_stage(b"100644 " + b"0" * 40 + b" 0 junk\tfoo\0")


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


def test_validate_dot_slash_prefix_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path("./src")


def test_validate_bare_dot_slash_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path("./")


def test_validate_dot_segment_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path("src/./foo")


def test_validate_double_slash_rejected():
    with pytest.raises(PathspecMagicError):
        validate_path("src//foo")


def test_validate_trailing_slash_still_ok():
    validate_path("src/foo/")


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


# -- parse_git_diff_names --------------------------------------------------


def test_parse_diff_names_empty():
    assert parse_git_diff_names(b"") == []


def test_parse_diff_names_single():
    assert parse_git_diff_names(b"a.go\0") == ["a.go"]


def test_parse_diff_names_multiple():
    assert parse_git_diff_names(b"a.go\0src/b.go\0") == ["a.go", "src/b.go"]


def test_parse_diff_names_utf8():
    raw = "docs/план.md\0".encode("utf-8")
    assert parse_git_diff_names(raw) == ["docs/план.md"]


def test_parse_diff_names_path_with_space():
    assert parse_git_diff_names(b"dir with space/b.go\0") == ["dir with space/b.go"]


# -- filter_source_set with changed_scope ---------------------------------


def test_filter_changed_scope_none_returns_full_source_set():
    stage = [IndexEntry("a.go", 0o100644), IndexEntry("b.go", 0o100644)]
    files, _ = filter_source_set(
        stage, [], ".", _always, _never, changed_scope=None
    )
    assert files == ["a.go", "b.go"]


def test_filter_changed_scope_narrows_to_intersection():
    stage = [
        IndexEntry("src/a.go", 0o100644),
        IndexEntry("src/b.go", 0o100644),
        IndexEntry("src/c.go", 0o100644),
    ]
    files, _ = filter_source_set(
        stage, [], ".", _always, _never,
        changed_scope={"src/a.go", "src/c.go"},
    )
    assert files == ["src/a.go", "src/c.go"]


def test_filter_empty_changed_scope_returns_no_files():
    stage = [IndexEntry("a.go", 0o100644)]
    files, warnings = filter_source_set(
        stage, [], ".", _always, _never, changed_scope=set()
    )
    assert files == []
    assert warnings == []


def test_filter_changed_scope_includes_untracked():
    stage = [IndexEntry("a.go", 0o100644)]
    files, _ = filter_source_set(
        stage, ["b.go"], ".", _always, _never,
        changed_scope={"a.go", "b.go"},
    )
    assert files == ["a.go", "b.go"]


def test_filter_changed_scope_composes_with_path_narrowing():
    stage = [
        IndexEntry("src/foo/a.go", 0o100644),
        IndexEntry("src/foo/b.go", 0o100644),
        IndexEntry("src/other/c.go", 0o100644),
    ]
    # Both a.go and c.go are in changed_scope; path filter restricts to src/foo.
    files, _ = filter_source_set(
        stage, [], "src/foo", _always, _never,
        changed_scope={"src/foo/a.go", "src/other/c.go"},
    )
    assert files == ["src/foo/a.go"]


def test_filter_changed_scope_still_excludes_gitlink():
    stage = [
        IndexEntry("a.go", 0o100644),
        IndexEntry("vendor/sub", GITLINK_MODE),
    ]
    # Submodule pointer in a diff (e.g., updated ref) is not lintable content.
    files, _ = filter_source_set(
        stage, [], ".", _always, _never,
        changed_scope={"a.go", "vendor/sub"},
    )
    assert files == ["a.go"]


def test_filter_changed_scope_still_excludes_symlink():
    stage = [
        IndexEntry("a.go", 0o100644),
        IndexEntry("link", SYMLINK_MODE),
    ]
    files, _ = filter_source_set(
        stage, [], ".", _always, _never,
        changed_scope={"a.go", "link"},
    )
    assert files == ["a.go"]


def test_filter_changed_scope_missing_worktree_file_warns_and_drops():
    stage = [
        IndexEntry("a.go", 0o100644),
        IndexEntry("gone.go", 0o100644),
    ]
    # `rm gone.go` (no `git rm`) leaves it staged and in a diff; worktree
    # missing -> same warning path as full-scan mode.
    files, warnings = filter_source_set(
        stage, [], ".", _in({"a.go"}), _never,
        changed_scope={"a.go", "gone.go"},
    )
    assert files == ["a.go"]
    assert warnings == [
        SourceWarning(path="gone.go", reason="tracked file missing from worktree"),
    ]


def test_filter_changed_scope_of_nonexistent_source_paths_drops_silently():
    """A file appearing only in `<ref>...HEAD` (later deleted from index and
    worktree) intersects to nothing and does not warn - it is not a
    tracked source anymore."""
    stage = [IndexEntry("a.go", 0o100644)]
    files, warnings = filter_source_set(
        stage, [], ".", _always, _never,
        changed_scope={"a.go", "removed.go"},
    )
    assert files == ["a.go"]
    assert warnings == []


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


# -- group_go_packages_by_module -------------------------------------------


def _roots(*dirs: str):
    return lambda d: d in dirs


def test_group_empty():
    assert group_go_packages_by_module([], _roots(".")) == ({}, [])


def test_group_root_module():
    groups, orphans = group_go_packages_by_module(["pkg", "."], _roots("."))
    assert groups == {".": [".", "pkg"]}
    assert orphans == []


def test_group_nested_module():
    groups, orphans = group_go_packages_by_module(
        ["go/a", "go/b/c"], _roots("go")
    )
    assert groups == {"go": ["go/a", "go/b/c"]}
    assert orphans == []


def test_group_module_root_is_its_own_package():
    groups, orphans = group_go_packages_by_module(["go"], _roots("go"))
    assert groups == {"go": ["go"]}
    assert orphans == []


def test_group_two_modules_and_orphan():
    groups, orphans = group_go_packages_by_module(
        ["alpha/pkg", "beta/lib", "scripts"], _roots("alpha", "beta")
    )
    assert groups == {"alpha": ["alpha/pkg"], "beta": ["beta/lib"]}
    assert orphans == ["scripts"]


def test_group_nearest_module_wins():
    """Go allows nested modules; the innermost go.mod owns the package."""
    groups, orphans = group_go_packages_by_module(
        ["go/sub/pkg", "go/other"], _roots("go", "go/sub")
    )
    assert groups == {"go/sub": ["go/sub/pkg"], "go": ["go/other"]}
    assert orphans == []


def test_group_no_module_anywhere_all_orphans():
    groups, orphans = group_go_packages_by_module(
        ["a", "b/c"], _roots()
    )
    assert groups == {}
    assert orphans == ["a", "b/c"]


# -- parse_check_attr (pure) ----------------------------------------------
# Stream shape verified against git 2.50.1: flat NUL-terminated triples
# `path\0attr\0value\0`, path-major with attributes in query order.


def _triples(*records: tuple[str, str, str]) -> bytes:
    return b"".join(
        f"{p}\0{a}\0{v}\0".encode("utf-8") for p, a, v in records
    )


def test_check_attr_empty_is_empty():
    assert parse_check_attr(b"") == {}


def test_check_attr_set_value_excludes():
    raw = _triples(("gen/api.pb.go", "linguist-generated", "set"))
    assert parse_check_attr(raw) == {"gen/api.pb.go": ["linguist-generated"]}


def test_check_attr_true_value_excludes():
    raw = _triples(("special.go", "linguist-generated", "true"))
    assert parse_check_attr(raw) == {"special.go": ["linguist-generated"]}


def test_check_attr_false_unset_unspecified_kept_in():
    raw = _triples(
        ("keep.go", "linguist-generated", "false"),
        ("unset.go", "linguist-generated", "unset"),
        ("plain.go", "linguist-generated", "unspecified"),
    )
    assert parse_check_attr(raw) == {}


def test_check_attr_multiple_set_attrs_deterministic_order():
    # Records may arrive unsorted; the returned attr list is the query order,
    # which is lexicographic (EXCLUSION_ATTRIBUTES).
    raw = _triples(
        ("both.go", "linguist-generated", "set"),
        ("both.go", "gitlab-generated", "set"),
        ("both.go", "linguist-vendored", "unspecified"),
    )
    assert parse_check_attr(raw) == {
        "both.go": ["gitlab-generated", "linguist-generated"]
    }


def test_check_attr_vendored_and_gitlab_spellings():
    raw = _triples(
        ("v/x.go", "linguist-vendored", "set"),
        ("g/y.go", "gitlab-generated", "set"),
    )
    assert parse_check_attr(raw) == {
        "v/x.go": ["linguist-vendored"],
        "g/y.go": ["gitlab-generated"],
    }


def test_check_attr_malformed_dangling_field_rejected():
    # Two fields where a triple is expected - a git bug, not silently dropped.
    with pytest.raises(ValueError):
        parse_check_attr(b"path\0linguist-generated\0")


def test_check_attr_unqueried_attribute_rejected():
    raw = _triples(("x.go", "linguist-documentation", "set"))
    with pytest.raises(ValueError):
        parse_check_attr(raw)


def test_check_attr_query_order_is_lexicographic():
    assert list(EXCLUSION_ATTRIBUTES) == sorted(EXCLUSION_ATTRIBUTES)


# -- narrow_files ----------------------------------------------------------


def test_narrow_files_dot_returns_all_sorted():
    assert narrow_files(["b.go", "a.go"], ".") == ["a.go", "b.go"]


def test_narrow_files_subtree_boundary():
    files = ["src/a.go", "src/nested/c.go", "srcfoo/d.go"]
    assert narrow_files(files, "src") == ["src/a.go", "src/nested/c.go"]


def test_narrow_files_changed_scope_intersects_then_narrows():
    files = ["src/a.go", "src/b.go", "other/c.go"]
    assert narrow_files(files, "src", changed_scope={"src/a.go", "other/c.go"}) == [
        "src/a.go"
    ]


def test_narrow_files_rejects_pathspec_magic():
    with pytest.raises(PathspecMagicError):
        narrow_files(["a.go"], "*.go")


# -- build_snapshot --------------------------------------------------------


def test_build_snapshot_splits_included_and_excluded_pairs():
    snap = build_snapshot(
        ["a.go", "gen/x.pb.go", "b.py"],
        {"gen/x.pb.go": ["linguist-generated"]},
        [],
    )
    assert snap.included == ["a.go", "b.py"]
    assert snap.excluded_pairs == [("gen/x.pb.go", "linguist-generated")]
    assert snap.excluded_files == frozenset({"gen/x.pb.go"})


def test_build_snapshot_multi_attr_one_pair_per_attr_sorted():
    snap = build_snapshot(
        ["both.go"],
        {"both.go": ["gitlab-generated", "linguist-generated"]},
        [],
    )
    assert snap.excluded_pairs == [
        ("both.go", "gitlab-generated"),
        ("both.go", "linguist-generated"),
    ]
    # A two-attribute file is one excluded file.
    assert snap.excluded_files == frozenset({"both.go"})


def test_build_snapshot_candidate_files_is_pre_exclusion_union():
    snap = build_snapshot(
        ["a.go", "gen/x.pb.go"],
        {"gen/x.pb.go": ["linguist-generated"]},
        [],
    )
    assert snap.candidate_files() == ["a.go", "gen/x.pb.go"]


def test_build_snapshot_carries_warnings():
    warn = [SourceWarning(path="gone.go", reason="tracked file missing from worktree")]
    snap = build_snapshot(["a.go"], {}, warn)
    assert snap.warnings == warn
    assert isinstance(snap, Snapshot)


# -- build_link_targets (Markdown link-target inventory) -------------------


def _lt_env(present: set[str], symlinks: set[str]):
    return (
        lambda p: p in present,
        lambda p: p in symlinks,
    )


def test_link_targets_regular_file_is_F():
    exists, is_symlink = _lt_env({"a.md", "b.md"}, set())
    stage = [IndexEntry("a.md", 0o100644), IndexEntry("b.md", 0o100755)]
    assert build_link_targets(stage, [], exists, is_symlink) == [("F", "a.md"), ("F", "b.md")]


def test_link_targets_tracked_symlink_is_L_not_dereferenced():
    exists, is_symlink = _lt_env({"real.md"}, {"link.md"})
    stage = [IndexEntry("real.md", 0o100644), IndexEntry("link.md", SYMLINK_MODE)]
    # Sorted by (path, kind): "link.md" precedes "real.md".
    assert build_link_targets(stage, [], exists, is_symlink) == [("L", "link.md"), ("F", "real.md")]


def test_link_targets_gitlink_is_G():
    exists, is_symlink = _lt_env(set(), set())
    stage = [IndexEntry("vendor/sub", GITLINK_MODE)]
    assert build_link_targets(stage, [], exists, is_symlink) == [("G", "vendor/sub")]


def test_link_targets_untracked_non_symlink_is_F():
    exists, is_symlink = _lt_env({"tracked.md"}, set())
    stage = [IndexEntry("tracked.md", 0o100644)]
    assert build_link_targets(stage, ["new.md"], exists, is_symlink) == [
        ("F", "new.md"),
        ("F", "tracked.md"),
    ]


def test_link_targets_untracked_symlink_dropped():
    # L is tracked-only; an untracked symlink mirrors the source set, which drops it.
    exists, is_symlink = _lt_env(set(), {"dangle.md"})
    assert build_link_targets([], ["dangle.md"], exists, is_symlink) == []


def test_link_targets_tracked_missing_from_worktree_dropped():
    # Same case the source set skips with a SourceWarning: not present -> not a target.
    exists, is_symlink = _lt_env(set(), set())
    stage = [IndexEntry("gone.md", 0o100644)]
    assert build_link_targets(stage, [], exists, is_symlink) == []


def test_link_targets_sorted_by_path_then_kind():
    exists, is_symlink = _lt_env({"z.md", "a.md"}, {"m.link"})
    stage = [
        IndexEntry("z.md", 0o100644),
        IndexEntry("m.link", SYMLINK_MODE),
        IndexEntry("a.md", 0o100644),
        IndexEntry("sub", GITLINK_MODE),
    ]
    assert build_link_targets(stage, [], exists, is_symlink) == [
        ("F", "a.md"),
        ("L", "m.link"),
        ("G", "sub"),
        ("F", "z.md"),
    ]

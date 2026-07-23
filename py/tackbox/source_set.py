"""Source-set derivation from the git index. Pure logic, no subprocess.

The source set is `git ls-files --cached --others --exclude-standard -z`.
Filesystem traversal never runs; `.git`, `node_modules`, `.venv`, `dist/`,
caches sit outside the set by construction.

Edge cases pinned by the plan:
- Gitlinks (index mode 160000, submodules) are excluded.
- Symlinks (index mode 120000, or untracked entries reported as symlinks
  by the caller) are excluded.
- Tracked files missing from the worktree emit a SourceWarning and are
  skipped; the run does not fail.
- `scope` narrowing uses a directory boundary: `src/foo` matches the file
  `src/foo` and anything under `src/foo/`, but not `src/foobar`.
- Pathspec magic (`:(exclude)`, `:!path`), glob metacharacters, absolute
  paths and parent traversals are refused - callers get no back door for
  configurable excludes.
- Non-canonical scopes (`./src`, `src//foo`, `.` segments) are refused:
  they would never match git index paths and silently narrow to nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

GITLINK_MODE = 0o160000
SYMLINK_MODE = 0o120000

_GLOB_CHARS = frozenset("*?[")
_PATHSPEC_MAGIC_PREFIXES = (":",)

# The three git attributes that exclude a file from the whole lint, in the
# deterministic (lexicographic) order used for every excluded-pair listing and
# the check-attr query. Extending this set is a plan-level change (R4).
EXCLUSION_ATTRIBUTES = ("gitlab-generated", "linguist-generated", "linguist-vendored")
# `set` and an explicit `=true` exclude; `false`, `unset`, `unspecified` do not.
_ATTR_SET_VALUES = frozenset({"set", "true"})


class PathspecMagicError(ValueError):
    """Raised when scope is a glob, pathspec magic, or otherwise unsafe."""


@dataclass(frozen=True)
class IndexEntry:
    path: str
    mode: int


@dataclass(frozen=True)
class SourceWarning:
    path: str
    reason: str


def parse_ls_files_stage(raw: bytes) -> list[IndexEntry]:
    """Parse `git ls-files -s -z`.

    Row format: `<mode> <sha> <stage>\\t<path>\\0`. Header must have
    exactly three space-separated fields; anything else is malformed.
    """
    entries: list[IndexEntry] = []
    for row in raw.split(b"\0"):
        if not row:
            continue
        header, sep, path = row.partition(b"\t")
        if not sep:
            raise ValueError(f"malformed ls-files -s row: {row!r}")
        header_fields = header.split(b" ")
        if len(header_fields) != 3:
            raise ValueError(f"malformed ls-files -s header: {header!r}")
        mode_bytes = header_fields[0]
        entries.append(
            IndexEntry(path=path.decode("utf-8"), mode=int(mode_bytes, 8))
        )
    return entries


def parse_ls_files_untracked(raw: bytes) -> list[str]:
    """Parse `git ls-files --others --exclude-standard -z`."""
    return [p.decode("utf-8") for p in raw.split(b"\0") if p]


def parse_check_attr(
    raw: bytes, attributes: tuple[str, ...] = EXCLUSION_ATTRIBUTES
) -> dict[str, list[str]]:
    """Parse `git check-attr -z --stdin <attr>...` into {path: [set attrs]}.

    The `-z` stream is flat NUL-terminated `path\\0attr\\0value\\0` triples. A
    path is listed only when a queried attribute resolved to `set` or `true`;
    `false`, `unset`, `unspecified` leave it out. Set attributes keep the query
    order (deterministic). A stream that is not a whole number of triples, or a
    triple naming an attribute we did not query, is malformed - a git bug, raised
    as ValueError (gitfiles turns it into the loud resolution-error type), never
    hidden.
    """
    order = {attr: i for i, attr in enumerate(attributes)}
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()  # trailing NUL terminates the last value
    if len(fields) % 3 != 0:
        raise ValueError(
            f"check-attr output is not whole triples: {len(fields)} fields"
        )
    result: dict[str, list[str]] = {}
    for i in range(0, len(fields), 3):
        path = fields[i].decode("utf-8")
        attr = fields[i + 1].decode("utf-8")
        value = fields[i + 2].decode("utf-8")
        if attr not in order:
            raise ValueError(f"check-attr returned unqueried attribute {attr!r}")
        if value in _ATTR_SET_VALUES:
            result.setdefault(path, []).append(attr)
    for attrs in result.values():
        attrs.sort(key=lambda a: order[a])
    return result


def parse_git_diff_names(raw: bytes) -> list[str]:
    """Parse `git diff --name-only -z` output (NUL-separated paths)."""
    return [p.decode("utf-8") for p in raw.split(b"\0") if p]


def validate_path(path: str) -> None:
    if path == "":
        raise PathspecMagicError("empty path")
    if any(c in path for c in _GLOB_CHARS):
        raise PathspecMagicError(f"glob characters in path: {path!r}")
    if path.startswith("!"):
        raise PathspecMagicError(f"negation prefix in path: {path!r}")
    for prefix in _PATHSPEC_MAGIC_PREFIXES:
        if path.startswith(prefix):
            raise PathspecMagicError(f"pathspec magic prefix in path: {path!r}")
    if path.startswith("/"):
        raise PathspecMagicError(f"absolute path not allowed: {path!r}")
    if ".." in path.split("/"):
        raise PathspecMagicError(f"parent traversal not allowed: {path!r}")
    if path != ".":
        core = path[:-1] if path.endswith("/") else path
        if any(seg in ("", ".") for seg in core.split("/")):
            raise PathspecMagicError(
                f"non-canonical path never matches the git index: {path!r}"
            )


def narrow_by_path(paths: Iterable[str], scope: str) -> list[str]:
    """Filter paths with directory-boundary semantics.

    - `.` returns everything.
    - Otherwise a path matches if it equals `scope` (with any trailing
      slash stripped) or begins with `scope/`.
    """
    if scope == ".":
        return list(paths)
    exact = scope.rstrip("/")
    dir_prefix = exact + "/"
    return [p for p in paths if p == exact or p.startswith(dir_prefix)]


def narrow_files(
    files: Iterable[str], scope: str, changed_scope: set[str] | None = None
) -> list[str]:
    """Narrow an already-pruned file list by `changed_scope` then `scope`.

    The snapshot is resolved once whole-tree; a scoped command narrows that list
    in Python rather than re-resolving. Same order as filter_source_set
    (changed_scope intersect, then directory-boundary narrow) and the same
    validate_path refusal, so the two agree on every scope.
    """
    validate_path(scope)
    pool = set(files)
    if changed_scope is not None:
        pool &= changed_scope
    return narrow_by_path(sorted(pool), scope)


@dataclass(frozen=True)
class Snapshot:
    """One tree inventory: included files (post-exclusion), the excluded
    `(path, attribute)` pairs, and the source warnings, all from one resolution.
    Whole-tree; scoped commands narrow `included` / `candidate_files()` in
    Python (narrow_files) with no second attribute resolution."""

    included: list[str]
    excluded_pairs: list[tuple[str, str]]
    warnings: list[SourceWarning]

    @property
    def excluded_files(self) -> frozenset[str]:
        return frozenset(path for path, _attr in self.excluded_pairs)

    def candidate_files(self) -> list[str]:
        """The pre-exclusion candidate set (included plus excluded), sorted. Used
        for scope semantics: a scope matching only excluded files is a success,
        so the exit-2 'matched nothing' test runs against this set."""
        return sorted(set(self.included) | self.excluded_files)


def build_snapshot(
    candidate_files: Iterable[str],
    excluded_map: dict[str, list[str]],
    warnings: list[SourceWarning],
) -> Snapshot:
    """Split the pruned candidate source set into a Snapshot. `excluded_map` is
    {path: [set attrs]} from the resolution seam over those same candidates;
    included files are sorted, pairs sorted by (path, attribute)."""
    excluded_pairs = sorted(
        (path, attr) for path, attrs in excluded_map.items() for attr in attrs
    )
    excluded = {path for path, _attr in excluded_pairs}
    included = sorted(f for f in candidate_files if f not in excluded)
    return Snapshot(
        included=included, excluded_pairs=excluded_pairs, warnings=list(warnings)
    )


def filter_source_set(
    stage_entries: list[IndexEntry],
    untracked_paths: list[str],
    scope: str,
    exists: Callable[[str], bool],
    is_symlink: Callable[[str], bool],
    changed_scope: set[str] | None = None,
) -> tuple[list[str], list[SourceWarning]]:
    """Apply edge-case filtering, then intersect with changed_scope, then
    narrow by `scope`.

    `exists` and `is_symlink` are injected because pure logic must not
    touch the filesystem; production wiring passes os.path.exists /
    os.path.islink from the CLI layer.

    `changed_scope=None` returns the full source set. A set (including
    empty) restricts the result to files that also appear in it. Edge-case
    pruning (gitlink / symlink / missing worktree) still runs first, so a
    diff entry for e.g. a submodule pointer cannot sneak past.
    """
    validate_path(scope)

    warnings: list[SourceWarning] = []
    tracked: list[str] = []
    for entry in stage_entries:
        if entry.mode == GITLINK_MODE:
            continue
        if entry.mode == SYMLINK_MODE:
            continue
        if not exists(entry.path):
            warnings.append(
                SourceWarning(
                    path=entry.path,
                    reason="tracked file missing from worktree",
                )
            )
            continue
        tracked.append(entry.path)

    untracked: list[str] = [p for p in untracked_paths if not is_symlink(p)]

    combined = set(tracked) | set(untracked)
    if changed_scope is not None:
        combined &= changed_scope
    return narrow_by_path(sorted(combined), scope), warnings


def build_link_targets(
    stage_entries: list[IndexEntry],
    untracked_paths: list[str],
    exists: Callable[[str], bool],
    is_symlink: Callable[[str], bool],
) -> list[tuple[str, str]]:
    """The Markdown link-target inventory, built from the RAW git listing before
    source-set filtering drops symlinks and gitlinks (that filtering is exactly
    why candidate_files is not enough here). Returns sorted `(kind, path)` pairs:

    - F: a source-set file - a tracked regular file present in the worktree, or an
      untracked non-symlink - taken BEFORE attribute exclusion (D016). An
      attribute-excluded file is still a valid link target: a target existing does
      not mean it is linted.
    - L: a tracked symlink (index mode 120000). Its target exists, is not
      dereferenced, and its fragment is never checked.
    - G: a gitlink root (index mode 160000, a submodule). A link target under such
      a prefix is skipped - a submodule is unverifiable from the superproject.

    Untracked symlinks are dropped, exactly as filter_source_set drops them; L is
    tracked-only. Tracked regular files missing from the worktree are dropped, the
    same SourceWarning case the source set skips.
    """
    targets: list[tuple[str, str]] = []
    for entry in stage_entries:
        if entry.mode == GITLINK_MODE:
            targets.append(("G", entry.path))
        elif entry.mode == SYMLINK_MODE:
            targets.append(("L", entry.path))
        elif exists(entry.path):
            targets.append(("F", entry.path))
    for path in untracked_paths:
        if not is_symlink(path):
            targets.append(("F", path))
    return sorted(targets, key=lambda t: (t[1], t[0]))


def files_to_go_packages(paths: Iterable[str]) -> list[str]:
    """Map `.go` files to sorted unique package directories.

    Files at repo root map to `.`. Non-`.go` paths are ignored. Callers
    prefix with `./` when handing the result to the `go` tool.
    """
    pkgs: set[str] = set()
    for p in paths:
        if not p.endswith(".go"):
            continue
        parent = p.rsplit("/", 1)[0] if "/" in p else "."
        pkgs.add(parent)
    return sorted(pkgs)


def group_go_packages_by_module(
    pkg_dirs: Iterable[str], is_module_root: Callable[[str], bool]
) -> tuple[dict[str, list[str]], list[str]]:
    """Group package dirs by their nearest enclosing Go module root.

    Walks each dir upward (inclusive) to the repo root looking for a dir
    satisfying `is_module_root`; nested modules resolve to the innermost
    one. All paths are repo-relative, the repo root itself is `.`.
    Returns ({module_root: sorted package dirs}, sorted orphans) where
    orphans have no enclosing module; callers warn and skip them.
    """
    groups: dict[str, list[str]] = {}
    orphans: list[str] = []
    for pkg in pkg_dirs:
        module = _nearest_module_root(pkg, is_module_root)
        if module is None:
            orphans.append(pkg)
        else:
            groups.setdefault(module, []).append(pkg)
    return {m: sorted(pkgs) for m, pkgs in groups.items()}, sorted(orphans)


def module_relative(module: str, pkg_dir: str) -> str:
    """Rebase a repo-relative package dir onto its module root."""
    if module == ".":
        return pkg_dir
    if pkg_dir == module:
        return "."
    return pkg_dir[len(module) + 1:]


def _nearest_module_root(
    pkg_dir: str, is_module_root: Callable[[str], bool]
) -> str | None:
    cur = pkg_dir
    while True:
        if is_module_root(cur):
            return cur
        if cur == ".":
            return None
        cur = cur.rsplit("/", 1)[0] if "/" in cur else "."

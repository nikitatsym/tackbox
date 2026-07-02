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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

GITLINK_MODE = 0o160000
SYMLINK_MODE = 0o120000

_GLOB_CHARS = frozenset("*?[")
_PATHSPEC_MAGIC_PREFIXES = (":",)


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

    Row format: `<mode> <sha> <stage>\\t<path>\\0`.
    """
    entries: list[IndexEntry] = []
    for row in raw.split(b"\0"):
        if not row:
            continue
        header, sep, path = row.partition(b"\t")
        if not sep:
            raise ValueError(f"malformed ls-files -s row: {row!r}")
        mode_bytes = header.split(b" ", 1)[0]
        entries.append(
            IndexEntry(path=path.decode("utf-8"), mode=int(mode_bytes, 8))
        )
    return entries


def parse_ls_files_untracked(raw: bytes) -> list[str]:
    """Parse `git ls-files --others --exclude-standard -z`."""
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


def filter_source_set(
    stage_entries: list[IndexEntry],
    untracked_paths: list[str],
    scope: str,
    exists: Callable[[str], bool],
    is_symlink: Callable[[str], bool],
) -> tuple[list[str], list[SourceWarning]]:
    """Apply edge-case filtering, then narrow by scope.

    `exists` and `is_symlink` are injected because pure logic must not
    touch the filesystem; production wiring passes os.path.exists /
    os.path.islink from the CLI layer.
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

    combined = sorted(set(tracked) | set(untracked))
    return narrow_by_path(combined, scope), warnings


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

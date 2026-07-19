"""The approval manifest: parse, provider seam, and the consistency predicate.

`.tackbox/approvals` lists every approved suppression marker as one line - an
address (D014) plus the exact marker text; multiplicity is repeated lines. The
invariant is bidirectional and stateless: every marker in the tree must be
covered by an entry, and every entry must match a live marker (an orphan is an
error). The check is a pure function of the tree.

    <repo-relative path>#<scope-chain>: <exact marker text>
    <repo-relative path>: <exact marker text>          (file scope)

The second form is file scope. Empty lines are ignored; every other line must
parse (a parse failure is an infra error, as with reporters). Reads go through
one provider - `load_approvals` - so an external backend is a drop-in.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import scopes

FILENAME = ".tackbox/approvals"


class ApprovalsError(Exception):
    """A manifest parse failure (CLI infra error, exit 1/2 as the caller sets)."""


@dataclass(frozen=True)
class Entry:
    address: str  # serialized `path` or `path#chain` (D014)
    marker: str  # exact marker text

    def line_text(self) -> str:
        return f"{self.address}: {self.marker}"


# -- escaping-aware splitting ----------------------------------------------


def _first_unescaped(s: str, ch: str, followed_by: str | None = None) -> int:
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2  # a backslash escapes the next character
            continue
        if c == ch and (followed_by is None or s[i + 1: i + 2] == followed_by):
            return i
        i += 1
    return -1


def _unescape(s: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def split_address(address: str) -> tuple[str, str]:
    """(repo-relative path, chain-string). Chain is "" for file scope. The path
    is unescaped; the chain stays serialized (used only for ordering)."""
    hsep = _first_unescaped(address, "#")
    if hsep < 0:
        return _unescape(address), ""
    return _unescape(address[:hsep]), address[hsep + 1:]


# -- parse + provider seam --------------------------------------------------


def parse(text: str) -> list[tuple[Entry, int]]:
    """(entry, 1-based line number) for each non-empty line. Line numbers back
    the orphan report's manifest location."""
    out: list[tuple[Entry, int]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        sep = _first_unescaped(line, ":", " ")
        if sep < 0:
            raise ApprovalsError(f"{FILENAME}:{lineno}: expected '<address>: <marker text>'")
        address, marker = line[:sep], line[sep + 2:]
        hsep = _first_unescaped(address, "#")
        path = address if hsep < 0 else address[:hsep]
        chain = "" if hsep < 0 else address[hsep + 1:]
        if not address or not marker or not path or (hsep >= 0 and not chain):
            raise ApprovalsError(f"{FILENAME}:{lineno}: empty path, chain, or marker text")
        out.append((Entry(address, marker), lineno))
    return out


def load_approvals(root: Path) -> Counter[Entry]:
    """The provider seam: every approvals read goes through here. The file
    backend is this plan's only implementation; the line format makes
    export/import to any other store trivial."""
    path = root / FILENAME
    if not path.is_file():
        return Counter()
    return Counter(entry for entry, _ln in parse(path.read_text(encoding="utf-8")))


# -- the consistency predicate ---------------------------------------------


@dataclass(frozen=True)
class Uncovered:
    entry: Entry
    file: str  # repo-relative path of the marker
    line: int


@dataclass(frozen=True)
class Orphan:
    entry: Entry
    line: int  # 1-based line in the manifest


@dataclass
class Report:
    uncovered: list[Uncovered] = field(default_factory=list)
    orphans: list[Orphan] = field(default_factory=list)
    unresolvable: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not (self.uncovered or self.orphans or self.unresolvable)

    def draft_lines(self) -> list[str]:
        """One entry line per uncovered occurrence, deterministic order."""
        return [u.entry.line_text() for u in self.uncovered]


# Canonical block texts (plan, user-approved) - fixed verbatim; only the
# indented value lines substitute, one per entry/path in deterministic order.
_UNAPPROVED = "Unapproved suppression marker (add the manifest line to request approval, or revert):"
_ORPHANED = "Orphaned approval (no matching marker; remove the line or restore the marker):"
_UNRESOLVABLE = ("Unresolvable file (syntax does not parse; its markers and approvals are "
                 "unverified - fix the syntax first):")
HEADER = "approvals (whole tree):"


def render_blocks(report: Report) -> list[str]:
    """The canonical lint-section lines (header + blocks), or [] when clean."""
    if report.ok():
        return []
    lines = [HEADER]
    if report.uncovered:
        lines.append(_UNAPPROVED)
        lines += [f"  {u.entry.line_text()}" for u in report.uncovered]
    if report.orphans:
        lines.append(_ORPHANED)
        lines += [f"  {o.entry.line_text()}" for o in report.orphans]
    if report.unresolvable:
        lines.append(_UNRESOLVABLE)
        lines += [f"  {p}" for p in report.unresolvable]
    return lines


def _sort_key_entry(entry: Entry) -> tuple[str, str, str]:
    path, chain = split_address(entry.address)
    return path, chain, entry.marker


def check(root: Path, files: list[str], marker_re: re.Pattern[str],
          is_lintable) -> Report:
    """Resolve the whole tree's marker inventory, load approvals, and report
    uncovered markers, orphaned entries, and unresolvable files. Deterministic
    order (path, chain, text). `files` is the whole-tree source set; the
    approvals check always covers it regardless of any lint scope."""
    parsed = parse((root / FILENAME).read_text(encoding="utf-8")) if (root / FILENAME).is_file() else []

    manifest_paths = {split_address(e.address)[0] for e, _ln in parsed}
    candidates = {
        f for f in files
        if is_lintable(f) and scopes.has_marker_text(root, f, marker_re)
    }
    candidates |= {f for f in files if is_lintable(f) and f in manifest_paths}

    occ_by_entry: dict[Entry, list[Uncovered]] = {}
    unresolvable: set[str] = set()
    for f in candidates:
        res = scopes.resolve_file(root, f, marker_re)
        if res.unresolvable:
            unresolvable.add(f)
            continue
        for rm in res.markers:
            e = Entry(rm.address, rm.marker)
            occ_by_entry.setdefault(e, []).append(Uncovered(e, f, rm.line))

    ent_by_entry: dict[Entry, list[int]] = {}
    for e, lineno in parsed:
        ent_by_entry.setdefault(e, []).append(lineno)

    report = Report(unresolvable=sorted(unresolvable))
    for entry in set(occ_by_entry) | set(ent_by_entry):
        if split_address(entry.address)[0] in unresolvable:
            continue  # reported as unresolvable, not as orphan/uncovered
        occ = sorted(occ_by_entry.get(entry, []), key=lambda u: u.line)
        ent = sorted(ent_by_entry.get(entry, []))
        covered = min(len(occ), len(ent))
        report.uncovered.extend(occ[covered:])
        report.orphans.extend(Orphan(entry, ln) for ln in ent[covered:])

    report.uncovered.sort(key=lambda u: (*_sort_key_entry(u.entry), u.line))
    report.orphans.sort(key=lambda o: (*_sort_key_entry(o.entry), o.line))
    return report

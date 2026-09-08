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

from . import proc, scopes
from .gitfiles import collect_snapshot

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
    """Load approved occurrence counts from the committed manifest format."""
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


_UNRESOLVABLE = "syntax does not parse; fix the syntax before verifying markers and approvals"
HEADER = "approvals (whole tree):"


def render_blocks(report: Report, findings=()) -> list[str]:
    """Render one actionable line per inconsistency, without approval entries."""
    suppressed: dict[tuple[str, int, str], set[str]] = {}
    for finding in findings:
        if not finding.suppressed or not finding.message:
            continue
        text = " ".join(finding.message.split())
        if not re.match(r"^(?:ERC|JV|TBX)\d+: ", text):
            text = f"{finding.rule}: {text}"
        key = (finding.file, finding.marker_line, finding.marker_kind)
        suppressed.setdefault(key, set()).add(text)
    lines = []
    for uncovered in report.uncovered:
        kind = uncovered.entry.marker.split(":", 1)[0]
        messages = suppressed.get((uncovered.file, uncovered.line, kind))
        location = f"{uncovered.file}:{uncovered.line}: "
        if messages:
            lines.append(
                location + "; ".join(sorted(messages))
                + f"; suppressed by an unapproved {kind} marker: fix the code, "
                + f"or the owner approves the marker in {FILENAME}"
            )
        else:
            lines.append(
                location + f"unapproved {kind} marker suppresses nothing here: remove it, "
                + f"or the owner approves it in {FILENAME}"
            )
    lines.extend(f"{FILENAME}:{orphan.line}: approval has no matching marker: remove the line"
                 for orphan in report.orphans)
    lines.extend(f"{path}: {_UNRESOLVABLE}" for path in report.unresolvable)
    return lines


def _sort_key_entry(entry: Entry) -> tuple[str, str, str]:
    path, chain = split_address(entry.address)
    return path, chain, entry.marker


def check(
    root: Path, files: list[str], marker_re: re.Pattern[str], is_lintable,
    only_paths: set[str] | None = None,
    added_lines: dict[str, set[int] | None] | None = None,
) -> Report:
    """Check tree consistency, or prioritize unchanged lines in a hook subset."""
    parsed = parse((root / FILENAME).read_text(encoding="utf-8")) if (root / FILENAME).is_file() else []
    if only_paths is not None:
        parsed = [(entry, line) for entry, line in parsed
                  if split_address(entry.address)[0] in only_paths]

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

    def line_key(path: str, line: int) -> tuple[bool, int]:
        lines = added_lines.get(path, ()) if added_lines is not None else ()
        return lines is None or line in lines, line

    report = Report(unresolvable=sorted(unresolvable))
    for entry in set(occ_by_entry) | set(ent_by_entry):
        if split_address(entry.address)[0] in unresolvable:
            continue  # reported as unresolvable, not as orphan/uncovered
        occ = sorted(occ_by_entry.get(entry, []), key=lambda u: line_key(u.file, u.line))
        ent = sorted(ent_by_entry.get(entry, []), key=lambda line: line_key(FILENAME, line))
        covered = min(len(occ), len(ent))
        report.uncovered.extend(occ[covered:])
        report.orphans.extend(Orphan(entry, ln) for ln in ent[covered:])

    report.uncovered.sort(key=lambda u: (*_sort_key_entry(u.entry), u.line))
    report.orphans.sort(key=lambda o: (*_sort_key_entry(o.entry), o.line))
    return report


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _git(root: Path, *args: str) -> bytes:
    return proc.run_bytes(["git", *args], cwd=root, capture_output=True, check=True).stdout


def session_report(root: Path, marker_re: re.Pattern[str], is_lintable) -> Report:
    """Return approvals debt introduced by the worktree's difference from HEAD."""
    head = proc.run_bytes(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
        cwd=root, capture_output=True,
    )
    if head.returncode not in (0, 1):
        head.check_returncode()
    if head.returncode == 1:
        snapshot = collect_snapshot(root)
        return check(root, snapshot.included, marker_re, is_lintable)
    changed = set(_git(root, "diff", "--name-only", "--no-renames", "-z", "HEAD", "--")
                  .decode("utf-8").split("\0")) - {""}
    untracked = set(_git(root, "ls-files", "--others", "--exclude-standard", "-z")
                    .decode("utf-8").split("\0")) - {""}
    changed |= untracked
    if not changed:
        return Report()
    added: dict[str, set[int] | None] = {path: None for path in untracked}
    removed: dict[str, set[int]] = {}
    for path in sorted(changed - untracked):
        patch = _git(root, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                     "--unified=0", "HEAD", "--", f":(literal){path}").decode("utf-8", errors="replace")
        added[path] = set()
        old_lines: set[int] = set()
        for line in patch.splitlines():
            match = _HUNK_RE.match(line)
            if match:
                old_start, old_count, new_start, new_count = match.groups()
                old_lines.update(range(int(old_start), int(old_start) + int(old_count or 1)))
                added[path].update(range(int(new_start), int(new_start) + int(new_count or 1)))
        if old_lines and any(marker_re.search(line[1:]) for line in patch.splitlines()
                             if line.startswith("-") and not line.startswith("---")):
            removed[path] = old_lines

    manifest = root / FILENAME
    parsed = parse(manifest.read_text(encoding="utf-8")) if manifest.is_file() else []
    manifest_added = added.get(FILENAME, set())
    new_entries = [(entry, line) for entry, line in parsed
                   if manifest_added is None or line in manifest_added]
    paths = changed | {split_address(entry.address)[0] for entry, _ in new_entries}
    snapshot = collect_snapshot(root, changed_scope=paths)
    current = check(
        root, snapshot.included, marker_re, is_lintable,
        only_paths=paths, added_lines=added,
    )
    debt = Report(
        uncovered=[u for u in current.uncovered
                   if u.file in added and (added[u.file] is None or u.line in added[u.file])],
        unresolvable=current.unresolvable,
    )
    deleted: Counter[Entry] = Counter()
    orphan_paths = {split_address(orphan.entry.address)[0] for orphan in current.orphans}
    for path in sorted(orphan_paths & removed.keys()):
        if not is_lintable(path):
            continue
        baseline = scopes.resolve_content(path, _git(root, "show", f"HEAD:{path}"), marker_re)
        if baseline.unresolvable:
            debt.unresolvable.append(path)
            continue
        deleted.update(Entry(marker.address, marker.marker) for marker in baseline.markers
                       if marker.line in removed[path])
    for orphan in current.orphans:
        if manifest_added is None or orphan.line in manifest_added:
            debt.orphans.append(orphan)
        elif deleted[orphan.entry]:
            debt.orphans.append(orphan)
            deleted[orphan.entry] -= 1
    debt.unresolvable = sorted(set(debt.unresolvable))
    return debt

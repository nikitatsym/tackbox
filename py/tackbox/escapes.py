"""tackbox escapes: the repo's bypass surface as JSON (D013).

An INVENTORY, not a gate. It enumerates every place code legitimately steps
off the paved road, so review tooling of any harness can consume it in one
cheap command:

* suppression markers (with their reason),
* `.tackbox/reporters` tier-2 declarations,
* notify / quiet lane choices (call sites).

Each entry carries file, line, the source text, and a context window of the
surrounding lines. The scan covers the lintable source set (the D012
predicate, `engines.lintable`) minus the attribute-excluded files, plus the
root `.tackbox/reporters`; every attribute-excluded file surfaces instead as
`attribute-excluded` entries (one per set attribute) - the whole-file bypass,
so its markers are dead (D012 cascade) and not listed as markers. Verb-site
detection is textual per language, so it may over-report - that is
observability, not a lint (D013). `--since <rev>` keeps only entries new
against that revision by content identity (kind, file, text/attribute); it
over-reports on moved code but never drops one silently, and the baseline is
attribute-aware (attributes as of the rev, via the seam's source override).
Exit is 0 whenever the command runs, entries or not; a bad `--since` rev, or a
git older than 2.40 on the `--since` path, is the reported nonzero - exit 1,
one stderr line.

No version banner (like the hook): stdout stays pure JSON and the command
needs no engine build - only git and the pure lintable predicate.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import TextIO

from .engines import EngineSpec, active_engines, lintable
from .gitfiles import collect_snapshot, resolve_attributes
from .reporters import FILENAME as REPORTERS_FILE

JSON_VERSION = 2

# The counts block always carries every kind (a stable schema), even absent.
# attribute-excluded counts unique files (one file may set several attributes);
# the others count entries.
_KINDS = ("marker", "reporter-decl", "notify-site", "quiet-site", "attribute-excluded")

# `git check-attr --source=<rev>` (the attribute-aware --since baseline) needs
# git >= 2.40; older git is an infra error on the --since path only.
_GIT_ATTR_SOURCE_MIN = (2, 40)


# -- verb-site detection (textual, per language; D013) --------------------
# Public verb names confirmed from the four runtime helpers:
#   go/report:      Quiet, Notify   -> attribute form .Quiet( / .Notify(
#   js/report.js:   reportQuiet, notify
#   tackbox_report: report_quiet, notify
#   Report.java:    quiet, notify   -> attribute form .quiet( / .notify(
# Word-boundaried so notifyAll( / snotify( / x.notifying( never match. A hit
# inside a comment or string is accepted over-report (an inventory, not a lint).
_ENGINE_VERBS: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {
    # engine id -> (quiet, notify)
    "erclint": (re.compile(r"\.Quiet\("), re.compile(r"\.Notify\(")),
    "javalint": (re.compile(r"\.quiet\("), re.compile(r"\.notify\(")),
    "pyrules": (re.compile(r"\breport_quiet\("), re.compile(r"\bnotify\(")),
    "tackbox-eslint": (re.compile(r"\breportQuiet\("), re.compile(r"\bnotify\(")),
}


def _verb_patterns(
    engines: list[EngineSpec],
) -> dict[str, tuple[re.Pattern[str], re.Pattern[str]]]:
    """ext -> (quiet, notify) regex, sourced from each engine's own extension
    set so a new JS extension needs no edit here. Only the language's primary
    engine carries verbs; jscpd / opengrep re-claim the same exts and are
    skipped, so every ext maps once."""
    out: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {}
    for engine in engines:
        verbs = _ENGINE_VERBS.get(engine.id)
        if verbs is None:
            continue
        for ext in engine.extensions:
            out[ext] = verbs
    return out


def run(
    repo_root: Path,
    *,
    since: str | None,
    context: int,
    marker_re: re.Pattern[str],
    out: TextIO,
    err: TextIO,
) -> int:
    """Emit the inventory JSON to `out`; return 0. A `--since` infra error (a bad
    rev, or a git < 2.40 that cannot resolve the attribute-aware baseline) writes
    one line to `err` and returns 1 - the inventory is not a gate, so entries or
    not is still exit 0. `marker_re` is injected by the CLI, which owns the
    canonical suppression-marker set, so escapes stays a leaf (no cli<->escapes
    cycle)."""
    window = max(0, context)
    engines = active_engines()
    verbs = _verb_patterns(engines)
    baseline = None
    if since is not None:
        version = _git_version()
        if version is not None and version < _GIT_ATTR_SOURCE_MIN:
            print(
                "tackbox: escapes --since needs git >= 2.40 for the "
                f"attribute-aware baseline (found {version[0]}.{version[1]})",
                file=err,
            )
            return 1
        paths, error = _ls_tree(repo_root, since)
        if error is not None:
            print(f"tackbox: {error}", file=err)
            return 1
        baseline = _rev_identities(repo_root, since, paths, engines, verbs, marker_re)
    current = _scan_tree(repo_root, engines, verbs, marker_re, window)
    current.sort(key=_sort_key)
    if baseline is not None:
        current = _keep_new(current, baseline)
    doc = {
        "version": JSON_VERSION,
        "since": since,
        "entries": current,
        "counts": _counts(current),
    }
    json.dump(doc, out, indent=2)
    out.write("\n")
    return 0


# -- scanning --------------------------------------------------------------


def _scan_tree(
    root: Path,
    engines: list[EngineSpec],
    verbs: dict,
    marker_re: re.Pattern[str],
    window: int,
) -> list[dict]:
    """Entries for the working tree from one attribute-aware snapshot: markers /
    verb sites in the included lintable files plus the root `.tackbox/reporters`,
    and one `attribute-excluded` entry per (file, attribute) of the excluded
    population. Excluded files' own markers are dead (D012) - not scanned."""
    snapshot = collect_snapshot(root)
    entries: list[dict] = []
    for rel in snapshot.included:
        if not lintable(rel, engines):
            continue
        content = _read_worktree(root, rel)
        if content is not None:
            entries += _scan_content(rel, content, verbs, marker_re, window)
    rep = _read_worktree(root, REPORTERS_FILE)
    if rep is not None:
        entries += _reporter_entries(rep, window)
    entries += _attribute_entries(snapshot.excluded_pairs)
    return entries


def _attribute_entries(excluded_pairs: list[tuple[str, str]]) -> list[dict]:
    """One `attribute-excluded` entry per (file, attribute). No line / text /
    context: the whole file is the bypass, and the source location carries no
    single line."""
    return [
        {"kind": "attribute-excluded", "file": file, "attribute": attr}
        for file, attr in excluded_pairs
    ]


def _rev_identities(
    root: Path,
    rev: str,
    paths: list[str],
    engines: list[EngineSpec],
    verbs: dict,
    marker_re: re.Pattern[str],
) -> Counter:
    """The identity multiset of the baseline `<rev>`, attribute-aware: markers /
    verb sites in the files that were lintable AND not attribute-excluded as of
    the rev, plus one attribute-excluded identity per (file, attribute) excluded
    then. A file excluded at the rev has its markers left out of the baseline, so
    removing the attribute since surfaces them as new (never a silent
    subtraction). Context is irrelevant to identity, so it runs at zero window."""
    rev_excluded = resolve_attributes(root, paths, source=rev)
    excluded_files = set(rev_excluded)
    entries: list[dict] = []
    for rel in paths:
        if rel in excluded_files or not lintable(rel, engines):
            continue
        content = _git_show(root, rev, rel)
        if content is not None:
            entries += _scan_content(rel, content, verbs, marker_re, 0)
    rep = _git_show(root, rev, REPORTERS_FILE)
    if rep is not None:
        entries += _reporter_entries(rep, 0)
    for rel in sorted(rev_excluded):
        for attr in rev_excluded[rel]:
            entries.append({"kind": "attribute-excluded", "file": rel, "attribute": attr})
    return Counter(_identity(e) for e in entries)


def _scan_content(
    rel: str,
    content: str,
    verbs: dict,
    marker_re: re.Pattern[str],
    window: int,
) -> list[dict]:
    """Marker and verb-site entries for one source file's content."""
    lines = content.split("\n")
    entries: list[dict] = []
    for lineno, text, reason in _iter_markers(content, marker_re):
        entries.append(_marker_entry(rel, lineno, text, reason, lines, window))
    pair = verbs.get(_ext(rel))
    if pair is not None:
        quiet_re, notify_re = pair
        for i, raw in enumerate(lines, 1):
            if quiet_re.search(raw):
                entries.append(_site_entry("quiet-site", rel, i, raw, lines, window))
            if notify_re.search(raw):
                entries.append(_site_entry("notify-site", rel, i, raw, lines, window))
    return entries


def _reporter_entries(content: str, window: int) -> list[dict]:
    """One reporter-decl per non-empty trimmed line. `.tackbox/reporters` has no
    comment syntax - `#` is the file#function separator - so every non-blank line
    is a declaration."""
    lines = content.split("\n")
    out: list[dict] = []
    for i, raw in enumerate(lines, 1):
        text = raw.strip()
        if text:
            out.append(
                {
                    "kind": "reporter-decl",
                    "file": REPORTERS_FILE,
                    "line": i,
                    "text": text,
                    "context": _context(lines, i, window),
                }
            )
    return out


def _iter_markers(content: str, marker_re: re.Pattern[str]):
    """(line, text, reason) per marker occurrence. text runs from the marker
    keyword to end of line (trimmed), the same extraction as the hook's
    `_markers`; reason is what follows the keyword's colon (trimmed, possibly
    empty - the `tackbox: lang=` marker carries none)."""
    for m in marker_re.finditer(content):
        nl = content.find("\n", m.start())
        eol = len(content) if nl < 0 else nl
        text = content[m.start():eol].strip()
        reason = content[m.end():eol].strip()
        line = content.count("\n", 0, m.start()) + 1
        yield line, text, reason


def _marker_entry(
    rel: str, line: int, text: str, reason: str, lines: list[str], window: int
) -> dict:
    return {
        "kind": "marker",
        "file": rel,
        "line": line,
        "text": text,
        "reason": reason,
        "context": _context(lines, line, window),
    }


def _site_entry(
    kind: str, rel: str, line: int, raw: str, lines: list[str], window: int
) -> dict:
    return {
        "kind": kind,
        "file": rel,
        "line": line,
        "text": raw.strip(),
        "context": _context(lines, line, window),
    }


def _context(lines: list[str], line: int, window: int) -> list[str]:
    """Source lines [line-window, line+window] inclusive (1-indexed), clipped to
    the file, each trimmed of trailing whitespace. The entry line itself is
    included, plain (not marked)."""
    lo = max(0, (line - 1) - window)
    hi = min(len(lines) - 1, (line - 1) + window)
    return [lines[i].rstrip() for i in range(lo, hi + 1)]


def _keep_new(entries: list[dict], baseline: Counter) -> list[dict]:
    """Entries whose identity is not covered by the baseline multiset, count
    aware: two identical entries now against one at `<rev>` yields one. The input
    order (already sorted) is preserved, so which duplicate survives is stable."""
    seen: Counter = Counter()
    out: list[dict] = []
    for e in entries:
        idt = _identity(e)
        seen[idt] += 1
        if seen[idt] > baseline.get(idt, 0):
            out.append(e)
    return out


def _identity(e: dict) -> tuple[str, str, str]:
    """Content identity for --since diffing: (kind, file, text) for the
    line-bearing kinds, (kind, file, attribute) for attribute-excluded."""
    if e["kind"] == "attribute-excluded":
        return (e["kind"], e["file"], e["attribute"])
    return (e["kind"], e["file"], e["text"])


def _sort_key(e: dict) -> tuple:
    """Total ordering over mixed entries: (file, kind, kind-subkey). kinds are
    lexicographic; the subkey is (line, text) for the line-bearing kinds and
    (attribute,) for attribute-excluded. Two entries sharing file and kind always
    carry the same subkey shape, so the mixed shapes never compare."""
    if e["kind"] == "attribute-excluded":
        subkey: tuple = (e["attribute"],)
    else:
        subkey = (e["line"], e["text"])
    return (e["file"], e["kind"], subkey)


def _counts(entries: list[dict]) -> dict[str, int]:
    c = Counter(e["kind"] for e in entries if e["kind"] != "attribute-excluded")
    attr_files = {e["file"] for e in entries if e["kind"] == "attribute-excluded"}
    return {
        k: (len(attr_files) if k == "attribute-excluded" else c.get(k, 0))
        for k in _KINDS
    }


# -- git / io --------------------------------------------------------------


def _git_version() -> tuple[int, int] | None:
    """(major, minor) of the host git, or None when it cannot be run or parsed
    (then the --since path proceeds and any real --source incompatibility fails
    loud at the seam)."""
    completed = subprocess.run(["git", "--version"], capture_output=True, text=True)
    if completed.returncode != 0:
        return None
    m = re.search(r"(\d+)\.(\d+)", completed.stdout)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _ls_tree(root: Path, rev: str) -> tuple[list[str], str | None]:
    """(repo-relative paths tracked at `<rev>`, error). A bad rev yields ([], one
    error line) - detected by returncode, not an exception, so the caller reports
    it as the one clean infra line instead of a swallowed catch."""
    r = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "-z", rev],
        cwd=root,
        capture_output=True,
    )
    if r.returncode != 0:
        detail = r.stderr.decode("utf-8", errors="replace").strip()
        first = detail.splitlines()[0] if detail else f"cannot resolve rev {rev!r}"
        return [], first
    paths = [p for p in r.stdout.decode("utf-8", errors="replace").split("\0") if p]
    return paths, None


def _git_show(root: Path, rev: str, rel: str) -> str | None:
    """`git show <rev>:<rel>` decoded utf-8/replace; None when the path is absent
    at that rev or is binary (a NUL byte - no marker/verb text to scan)."""
    r = subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=root, capture_output=True)
    if r.returncode != 0:
        return None
    return _decode(r.stdout)


def _read_worktree(root: Path, rel: str) -> str | None:
    """Worktree text at root/rel (utf-8/replace); None when the path is gone
    (a race) or binary. is_file() guards the race, mirroring the hook's read."""
    p = root / rel
    if not p.is_file():
        return None
    return _decode(p.read_bytes())


def _decode(data: bytes) -> str | None:
    if b"\x00" in data:
        return None
    return data.decode("utf-8", errors="replace")


def _ext(path: str) -> str:
    dot = path.rfind(".")
    return path[dot:] if dot >= 0 else ""

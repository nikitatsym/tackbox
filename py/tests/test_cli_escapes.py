"""Step 6 acceptance: `tackbox escapes` bypass-surface inventory (D013).

Drives `python -m tackbox.cli escapes` against tmp git repos and pins the JSON
contract, the lintable-only scope, --since content-identity diffing (including
the over-report on moved code, never a silent drop), reason extraction, exit
semantics, context windows, and verb-site word boundaries.

Fixtures live only in the tmp repos, so tackbox self-lint never sees their
marker text. escapes needs no engine build (only git + the pure lintable
predicate), so these run without a go/node/java toolchain.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import commit_all, git, init_repo, tackbox_env


def _run(repo: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "escapes", *flags],
        cwd=repo,
        env=tackbox_env(),
        capture_output=True,
        text=True,
    )


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _doc(r: subprocess.CompletedProcess) -> dict:
    """Parse stdout as JSON, asserting the round-trip (stdout is pure JSON)."""
    assert r.returncode == 0, f"exit={r.returncode} stderr={r.stderr!r}"
    return json.loads(r.stdout)


def _texts(doc: dict, kind: str) -> list[str]:
    return [e["text"] for e in doc["entries"] if e["kind"] == kind]


def _find(doc: dict, kind: str, file: str, needle: str) -> dict | None:
    for e in doc["entries"]:
        if e["kind"] == kind and e["file"] == file and needle in e["text"]:
            return e
    return None


# -- fixtures for the full inventory --------------------------------------

# Five keyword markers, one per line, lines 3..7 (line 1 package, line 2 blank).
MARKERS_GO = (
    "package app\n"
    "\n"
    "// no-report: central boundary already captures this\n"
    "// parse-skip: optional config, absence is acceptable\n"
    "// nil-return: sentinel documented in the type contract\n"
    "// test-skip: flaky under race, tracked in issue 123\n"
    "// dup-ok: intentional second capture at the boundary\n"
)

# notify + report_quiet on lines 2 and 3.
SINKS_PY = (
    "def handle(e):\n"
    '    notify("you appear to be offline", e, {}, "net.offline")\n'
    '    report_quiet("degraded, using cache", e, {}, "cache.stale")\n'
)

# report.Notify + report.Quiet on lines 4 and 5.
SINKS_GO = (
    "package app\n"
    "\n"
    "func handle(err error) {\n"
    '\treport.Notify(ctx, "offline", err, nil, "net.offline")\n'
    '\treport.Quiet(ctx, "degraded", err, nil, "cache.stale")\n'
    "}\n"
)

REPORTERS = (
    "src/app/errors.py#report_api_error: central API error sink\n"
    "go/net.go#reportNet: network failure capture sink\n"
)


def _full_repo(tmp_path: Path) -> Path:
    _write(tmp_path, "dev.py", "# fixture dev script\n")
    _write(tmp_path, "markers.go", MARKERS_GO)
    _write(tmp_path, "sinks.py", SINKS_PY)
    _write(tmp_path, "sinks.go", SINKS_GO)
    _write(tmp_path, ".tackbox/reporters", REPORTERS)
    init_repo(tmp_path, commit=True)
    return tmp_path


def test_full_inventory_every_kind(tmp_path):
    """One marker of each kind, two reporter decls, a notify + quiet site in two
    languages -> every entry with correct kind/file/line/reason; counts match;
    entries sorted by (file, line)."""
    repo = _full_repo(tmp_path)
    doc = _doc(_run(repo))

    assert doc["version"] == 1
    assert doc["since"] is None
    assert doc["counts"] == {
        "marker": 5,
        "reporter-decl": 2,
        "notify-site": 2,
        "quiet-site": 2,
    }

    # Markers: kind, file, line, reason for each keyword.
    expected = {
        3: ("no-report:", "central boundary already captures this"),
        4: ("parse-skip:", "optional config, absence is acceptable"),
        5: ("nil-return:", "sentinel documented in the type contract"),
        6: ("test-skip:", "flaky under race, tracked in issue 123"),
        7: ("dup-ok:", "intentional second capture at the boundary"),
    }
    markers = {e["line"]: e for e in doc["entries"] if e["kind"] == "marker"}
    assert set(markers) == set(expected)
    for line, (kw, reason) in expected.items():
        e = markers[line]
        assert e["file"] == "markers.go"
        assert e["text"].startswith(kw)
        assert e["reason"] == reason

    # Reporter declarations: verbatim trimmed lines, file is the reporters file.
    decls = _texts(doc, "reporter-decl")
    assert "src/app/errors.py#report_api_error: central API error sink" in decls
    assert "go/net.go#reportNet: network failure capture sink" in decls
    assert all(
        e["file"] == ".tackbox/reporters"
        for e in doc["entries"]
        if e["kind"] == "reporter-decl"
    )

    # notify / quiet sites in both languages, with lines.
    assert _find(doc, "notify-site", "sinks.py", "notify(")["line"] == 2
    assert _find(doc, "quiet-site", "sinks.py", "report_quiet(")["line"] == 3
    assert _find(doc, "notify-site", "sinks.go", "report.Notify(")["line"] == 4
    assert _find(doc, "quiet-site", "sinks.go", "report.Quiet(")["line"] == 5

    # Entries sorted by (file, line).
    keys = [(e["file"], e["line"]) for e in doc["entries"]]
    assert keys == sorted(keys)


def test_lintable_scope_only(tmp_path):
    """ADVERSARIAL: a marker in an unlintable file (.py.txt) and in a Go
    testdata/ path is absent (D013 scope = the lintable predicate); the same
    marker text in a live .py is present."""
    _write(tmp_path, "dead.py.txt", "# no-report: dead marker in a txt fixture aaaa\n")
    _write(
        tmp_path,
        "go/testdata/x.go",
        "// no-report: dead marker in go testdata bbbb\n",
    )
    _write(tmp_path, "live.py", "# no-report: live marker in a py file cccc\n")
    init_repo(tmp_path, commit=True)

    doc = _doc(_run(tmp_path))
    reasons = [e.get("reason") for e in doc["entries"] if e["kind"] == "marker"]
    assert "live marker in a py file cccc" in reasons
    assert "dead marker in a txt fixture aaaa" not in reasons
    assert "dead marker in go testdata bbbb" not in reasons
    # Only the one live marker, nothing from the unlintable files.
    assert doc["counts"]["marker"] == 1


def test_since_lists_only_new_entries(tmp_path):
    """--since: a committed baseline with one marker; add a second marker and a
    new notify site -> only the new ones listed; `since` set; unchanged absent."""
    _write(tmp_path, "a.go", "// no-report: baseline marker committed first aaaa\n")
    init_repo(tmp_path, commit=True)

    # Uncommitted: a second marker in the same file + an untracked notify site.
    _write(
        tmp_path,
        "a.go",
        "// no-report: baseline marker committed first aaaa\n"
        "// no-report: second marker added after baseline bbbb\n",
    )
    _write(tmp_path, "b.py", 'notify("offline banner", e, {}, "net.x")\n')

    doc = _doc(_run(tmp_path, "--since", "HEAD"))
    assert doc["since"] == "HEAD"
    reasons = [e.get("reason") for e in doc["entries"] if e["kind"] == "marker"]
    assert "second marker added after baseline bbbb" in reasons
    # The unchanged baseline marker is covered by the rev and must not appear.
    assert "baseline marker committed first aaaa" not in reasons
    # The new notify site is new (b.py absent at HEAD).
    assert _find(doc, "notify-site", "b.py", "notify(") is not None
    assert doc["counts"] == {
        "marker": 1,
        "reporter-decl": 0,
        "notify-site": 1,
        "quiet-site": 0,
    }


def test_since_moved_file_over_reports(tmp_path):
    """ADVERSARIAL never a silent drop: git mv a marker-bearing file -> the
    marker reappears in --since output at the new path (over-report on moved
    code, per D013), it is never silently dropped."""
    _write(tmp_path, "old.go", "// no-report: marker that survives the move dddd\n")
    init_repo(tmp_path, commit=True)

    git(tmp_path, "mv", "old.go", "new.go")

    doc = _doc(_run(tmp_path, "--since", "HEAD"))
    moved = _find(doc, "marker", "new.go", "survives the move dddd")
    assert moved is not None, f"moved marker dropped: {doc['entries']}"
    assert moved["reason"] == "marker that survives the move dddd"
    # Its old identity (old.go) is gone from the worktree, so it is not present.
    assert _find(doc, "marker", "old.go", "survives the move dddd") is None


def test_reason_extraction_trailing_ws_crlf_and_lang(tmp_path):
    """Reason trimmed of trailing spaces and CRLF; the markdown `tackbox: lang=`
    marker yields an empty reason."""
    _write(tmp_path, "spaces.go", "// no-report: reason with trailing spaces eeee   \n")
    (tmp_path / "crlf.go").write_bytes(
        b"package p\r\n// no-report: crlf reason should trim ffff  \r\n"
    )
    _write(tmp_path, "guide.md", "# Guide\n\ntackbox: lang=go\n")
    init_repo(tmp_path, commit=True)

    doc = _doc(_run(tmp_path))
    reasons = [e.get("reason") for e in doc["entries"] if e["kind"] == "marker"]
    assert "reason with trailing spaces eeee" in reasons  # no trailing spaces
    assert "crlf reason should trim ffff" in reasons  # no trailing \r or spaces

    lang = _find(doc, "marker", "guide.md", "tackbox: lang=go")
    assert lang is not None
    assert lang["text"] == "tackbox: lang=go"
    assert lang["reason"] == ""


def test_exit_semantics(tmp_path):
    """Entries present -> exit 0 + valid JSON. --since garbage-rev -> exit 1 +
    one stderr line, empty stdout."""
    repo = _full_repo(tmp_path)

    ok = _run(repo)
    assert ok.returncode == 0
    assert json.loads(ok.stdout)["entries"]  # non-empty, round-trips

    bad = _run(repo, "--since", "garbage-rev-nope")
    assert bad.returncode == 1
    assert bad.stdout == ""  # nothing on stdout
    assert bad.stderr.strip().count("\n") == 0  # exactly one line
    assert bad.stderr.startswith("tackbox: ")


def test_context_window_sizes_and_clipping(tmp_path):
    """--context 1 vs default 3 change the window; the window clips at file edges
    without error. Marker on line 5 (interior) and line 1 (top edge)."""
    interior = "".join(f"// line {i}\n" for i in range(1, 5))
    interior += "// no-report: interior marker for the context window gggg\n"
    interior += "".join(f"// line {i}\n" for i in range(6, 10))
    _write(tmp_path, "interior.go", interior)
    top = "// no-report: top edge marker clipped window hhhh\n"
    top += "".join(f"// tail {i}\n" for i in range(1, 6))
    _write(tmp_path, "top.go", top)
    init_repo(tmp_path, commit=True)

    d1 = _doc(_run(tmp_path, "--context", "1"))
    d3 = _doc(_run(tmp_path, "--context", "3"))

    i1 = _find(d1, "marker", "interior.go", "interior marker")
    i3 = _find(d3, "marker", "interior.go", "interior marker")
    assert len(i1["context"]) == 3  # line-1 .. line+1
    assert len(i3["context"]) == 7  # line-3 .. line+3

    # Top-edge marker (line 1): the window clips below the file with no error.
    # --context 3 gives the entry line plus the 3 lines after it (nothing before).
    top3 = _find(d3, "marker", "top.go", "top edge marker")
    assert top3["line"] == 1
    assert len(top3["context"]) == 4  # line 1 (self) + lines 2..4, clipped at top


def test_verb_word_boundaries(tmp_path):
    """notifyAll( / x.notifying( / snotify( do not match; a real notify( does."""
    _write(
        tmp_path,
        "wb.py",
        'notify("the one real notice", e, {}, "k.real")\n'
        'x.notifyAll("java monitor lookalike")\n'
        'x.notifying("present participle")\n'
        'snotify("prefixed identifier")\n',
    )
    init_repo(tmp_path, commit=True)

    doc = _doc(_run(tmp_path))
    notify_lines = [e["line"] for e in doc["entries"] if e["kind"] == "notify-site"]
    assert notify_lines == [1], f"only the real notify( should match: {doc['entries']}"
    assert doc["counts"]["notify-site"] == 1
    assert doc["counts"]["quiet-site"] == 0

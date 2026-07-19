"""The approval manifest: parse, provider seam, the consistency predicate, the
draft generator, canonical texts, and the CLI surfaces (standalone + lint).

Marker-bearing source is generated inline into tmp repos (never tracked
fixtures), so tackbox self-lint never scans it. The canonical block/section
texts below are the plan's fixed verbatim strings - the fixture is the spec.
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from conftest import init_repo, tackbox_env

from tackbox import approvals, scopes
from tackbox.approvals import Entry
from tackbox.cli import _MARKER_RE

IS_LINTABLE = lambda rel: scopes.language_for(rel) is not None  # noqa: E731

# The five canonical texts (plan, user-approved) - the spec, pinned here.
UNAPPROVED = "Unapproved suppression marker (add the manifest line to request approval, or revert):"
ORPHANED = "Orphaned approval (no matching marker; remove the line or restore the marker):"
UNRESOLVABLE = ("Unresolvable file (syntax does not parse; its markers and approvals are "
                "unverified - fix the syntax first):")
HEADER = "approvals (whole tree):"


def build(tmp_path: Path, files: dict[str, str], manifest: str | None = None) -> Path:
    root = tmp_path
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    if manifest is not None:
        (root / ".tackbox").mkdir(parents=True, exist_ok=True)
        (root / approvals.FILENAME).write_text(manifest, encoding="utf-8")
    return root


def check(root: Path) -> approvals.Report:
    files = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
    return approvals.check(root, files, _MARKER_RE, IS_LINTABLE)


# -- parse + escaping-aware split ------------------------------------------

def test_parse_file_and_chain_scope():
    entries = approvals.parse(
        "app/svc.py: no-report: module level\n"
        "app/svc.py#Handler.process: no-report: legacy path\n"
    )
    assert [e.address for e, _ in entries] == ["app/svc.py", "app/svc.py#Handler.process"]
    assert [e.marker for e, _ in entries] == ["no-report: module level", "no-report: legacy path"]


def test_parse_ignores_empty_lines_and_tracks_linenos():
    entries = approvals.parse("\na.py: no-report: x\n\n\nb.py: no-report: y\n")
    assert [(e.address, ln) for e, ln in entries] == [("a.py", 2), ("b.py", 5)]


def test_parse_separator_is_first_unescaped_colon_space():
    # The marker text itself carries `: `; the split must be the FIRST unescaped
    # `: `, which sits between address and the keyword.
    [(entry, _ln)] = approvals.parse("a.py#f: parse-skip: reason: with colons\n")
    assert entry.address == "a.py#f"
    assert entry.marker == "parse-skip: reason: with colons"


def test_parse_escaped_colon_in_address_is_not_the_separator():
    # A heading titled `Foo: Bar` serializes its colon as `\:`; that escaped
    # colon-space must not be mistaken for the address/marker separator.
    [(entry, _ln)] = approvals.parse(r"d.md#Foo\: Bar: no-report: r" + "\n")
    assert entry.address == r"d.md#Foo\: Bar"
    assert entry.marker == "no-report: r"
    assert approvals.split_address(entry.address) == ("d.md", r"Foo\: Bar")


def test_parse_escaped_hash_in_path():
    path, chain = approvals.split_address(r"docs/a\#b.md#Heading")
    assert path == "docs/a#b.md" and chain == "Heading"


@pytest.mark.parametrize("bad", ["no-separator-here", "a.py:no-space-after-colon", ": marker", "a.py#: m"])
def test_parse_errors_raise_loudly(bad):
    with pytest.raises(approvals.ApprovalsError):
        approvals.parse(bad + "\n")


def test_load_approvals_provider_seam(tmp_path):
    root = build(tmp_path, {}, manifest="a.py: no-report: x\na.py: no-report: x\nb.py: no-report: y\n")
    got = approvals.load_approvals(root)
    assert got == Counter({Entry("a.py", "no-report: x"): 2, Entry("b.py", "no-report: y"): 1})


def test_load_approvals_absent_is_empty(tmp_path):
    assert approvals.load_approvals(tmp_path) == Counter()


# -- the consistency predicate ---------------------------------------------

def test_clean_tree_is_consistent(tmp_path):
    root = build(
        tmp_path,
        {"a.py": "def f():\n    x() # no-report: covered\n"},
        manifest="a.py#f: no-report: covered\n",
    )
    report = check(root)
    assert report.ok()
    assert approvals.render_blocks(report) == []


def test_uncovered_marker_is_reported(tmp_path):
    root = build(tmp_path, {"a.py": "def f():\n    x() # no-report: unapproved\n"}, manifest="")
    report = check(root)
    assert not report.ok()
    assert [(u.entry.address, u.file, u.line) for u in report.uncovered] == [("a.py#f", "a.py", 2)]
    assert report.orphans == [] and report.unresolvable == []


def test_orphan_entry_is_reported(tmp_path):
    root = build(tmp_path, {"a.py": "x = 1\n"}, manifest="a.py#gone: no-report: no such marker\n")
    report = check(root)
    assert [(o.entry.address, o.line) for o in report.orphans] == [("a.py#gone", 1)]
    assert report.uncovered == []


def test_multiplicity_pairs_by_count(tmp_path):
    # Two identical markers, one entry -> one uncovered (the tail); flip -> orphan.
    two_markers = "def f():\n    a() # dup-ok: same\n    b() # dup-ok: same\n"
    root = build(tmp_path, {"a.py": two_markers}, manifest="a.py#f: dup-ok: same\n")
    report = check(root)
    assert len(report.uncovered) == 1 and report.uncovered[0].line == 3
    assert report.orphans == []

    root2 = build(tmp_path, {"a.py": two_markers},
                  manifest="a.py#f: dup-ok: same\na.py#f: dup-ok: same\na.py#f: dup-ok: same\n")
    report2 = check(root2)
    assert report2.uncovered == [] and len(report2.orphans) == 1
    assert report2.orphans[0].line == 3  # the third manifest line is the orphaned tail


# A mid-body missing brace yields grammar ERROR nodes (an unclosed class at EOF
# is recovered without one); trailing `void b()` forces the error.
BROKEN_JAVA = (
    "class C {\n  void a() {\n    if (true) {\n      x(); // no-report: broken\n"
    "  }\n  void b() { y(); }\n}\n"
)


def test_unresolvable_file_reported_not_orphaned(tmp_path):
    # A file that does not parse and carries a marker: its markers/entries are
    # unverified -> reported as unresolvable, never as orphan/uncovered.
    root = build(tmp_path, {"C.java": BROKEN_JAVA}, manifest="C.java#C.a(): no-report: broken\n")
    report = check(root)
    assert report.unresolvable == ["C.java"]
    assert report.uncovered == [] and report.orphans == []


def test_deterministic_order(tmp_path):
    files = {
        "b.py": "y = 1 # no-report: b-file\n",
        "a.py": "def g():\n    x() # no-report: a-g\ndef f():\n    x() # no-report: a-f\n",
    }
    root = build(tmp_path, files, manifest="")
    report = check(root)
    # sorted by (path, chain, text): a.py#f, a.py#g, then b.py (file scope).
    assert [u.entry.address for u in report.uncovered] == ["a.py#f", "a.py#g", "b.py"]


def test_at_escape_adversarial_matching(tmp_path):
    # Manifest covers only the literal-titled `A@2` heading; the second sibling
    # `A` (serialized `A@2`) stays uncovered - the escape distinguishes them.
    md = (
        "# A@2\n\n<!-- no-report: literal -->\n\n"
        "# A\n\n<!-- no-report: first -->\n\n"
        "# A\n\n<!-- no-report: second -->\n"
    )
    # HTML-comment marker text runs to end of line (unchanged _markers), so it
    # carries the trailing ` -->`; the entry must match it exactly.
    root = build(tmp_path, {"d.md": md}, manifest="d.md#A\\@2: no-report: literal -->\n")
    report = check(root)
    uncovered = {u.entry.address for u in report.uncovered}
    assert uncovered == {"d.md#A", "d.md#A@2"}
    assert report.orphans == []  # the `A\@2` entry matched its own marker, not spuriously


# -- canonical texts + draft ------------------------------------------------

def test_render_blocks_canonical_texts(tmp_path):
    root = build(
        tmp_path,
        {"a.py": "def f():\n    x() # no-report: uncovered\n"},
        manifest="a.py#gone: no-report: orphan\n",
    )
    lines = approvals.render_blocks(check(root))
    assert lines[0] == HEADER
    assert UNAPPROVED in lines and ORPHANED in lines
    assert "  a.py#f: no-report: uncovered" in lines
    assert "  a.py#gone: no-report: orphan" in lines


def test_draft_lines_are_entries_for_uncovered(tmp_path):
    root = build(tmp_path, {"a.py": "def f():\n    x() # no-report: needs approval\n"}, manifest="")
    report = check(root)
    assert report.draft_lines() == ["a.py#f: no-report: needs approval"]


def test_draft_roundtrips_into_consistency(tmp_path):
    # Drafting then approving every line makes the tree consistent.
    root = build(tmp_path, {"a.py": "def f():\n    x() # no-report: r\n"}, manifest="")
    draft = "\n".join(check(root).draft_lines()) + "\n"
    (root / approvals.FILENAME).write_text(draft, encoding="utf-8")
    assert check(root).ok()


# -- CLI: standalone approvals + lint verdict ------------------------------

def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tackbox.cli", *args],
        cwd=root, env=tackbox_env(), capture_output=True, text=True,
    )


@pytest.fixture()
def repo(tmp_path) -> Path:
    (tmp_path / "dev.py").write_text("# marker\n")
    (tmp_path / "a.py").write_text("def f():\n    x() # no-report: unapproved marker\n")
    init_repo(tmp_path, commit=True)
    return tmp_path


def test_cli_approvals_inconsistent_exits_2(repo):
    r = _run(repo, "approvals")
    assert r.returncode == 2, r.stderr
    assert UNAPPROVED in r.stdout
    assert "a.py#f: no-report: unapproved marker" in r.stdout


def test_cli_approvals_draft_exits_0_and_bootstraps(repo):
    draft = _run(repo, "approvals", "--draft")
    assert draft.returncode == 0, draft.stderr
    assert draft.stdout.strip() == "a.py#f: no-report: unapproved marker"
    (repo / ".tackbox").mkdir()
    (repo / approvals.FILENAME).write_text(draft.stdout)
    ok = _run(repo, "approvals")
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_cli_approvals_draft_incomplete_on_unresolvable_exits_2(tmp_path):
    (tmp_path / "dev.py").write_text("# marker\n")
    (tmp_path / "B.java").write_text(BROKEN_JAVA)
    init_repo(tmp_path, commit=True)
    draft = _run(tmp_path, "approvals", "--draft")
    assert draft.returncode == 2  # an unresolvable file makes the draft incomplete
    assert "unresolvable" in draft.stderr.lower()


def test_cli_lint_prints_whole_tree_header_and_fails(repo):
    r = _run(repo, "lint", ".")
    assert HEADER in r.stdout
    assert UNAPPROVED in r.stdout
    assert r.returncode == 1  # approvals inconsistency counts as a finding


def test_cli_lint_approvals_is_whole_tree_even_when_scope_is_clean(tmp_path):
    # Scope `clean/` has no markers, but the tree is red outside it: the
    # approvals check must still cover the whole tree and fail.
    (tmp_path / "dev.py").write_text("# marker\n")
    (tmp_path / "clean").mkdir()
    (tmp_path / "clean" / "ok.py").write_text("x = 1\n")
    (tmp_path / "dirty.py").write_text("def f():\n    x() # no-report: outside scope\n")
    init_repo(tmp_path, commit=True)
    r = _run(tmp_path, "lint", "clean")
    assert HEADER in r.stdout, r.stdout + r.stderr
    assert "dirty.py#f: no-report: outside scope" in r.stdout
    assert r.returncode == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

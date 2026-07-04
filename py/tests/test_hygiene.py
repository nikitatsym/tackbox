"""Fixtures for the root hygiene.py checker (dev.py lint hygiene-fold).

These pin the four check-only equivalents of the removed pre-commit hooks
(conflict markers, YAML parse, trailing whitespace, final newline). Each check
is exercised adversarially: plant the exact violation and prove it is caught,
plus a false-positive guard for the conflict separator. hygiene.py is loaded
from the repo root the same way test_dev.py loads dev.py; pyyaml is present in
the pytest env (dev dependency), which is why the checker can `import yaml`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_hygiene():
    spec = importlib.util.spec_from_file_location("hygienecheck", ROOT / "hygiene.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hygiene = _load_hygiene()


def test_clean_file_no_findings(tmp_path):
    (tmp_path / "a.txt").write_text("hello\nworld\n")
    assert hygiene.findings(["a.txt"], tmp_path) == []


def test_trailing_whitespace_flagged(tmp_path):
    (tmp_path / "a.txt").write_text("ok\nbad \ntab\t\nfine\n")
    fs = hygiene.findings(["a.txt"], tmp_path)
    assert "a.txt:2: trailing whitespace" in fs
    assert "a.txt:3: trailing whitespace" in fs
    assert [f for f in fs if "trailing whitespace" in f] == [
        "a.txt:2: trailing whitespace",
        "a.txt:3: trailing whitespace",
    ]


def test_trailing_after_final_newline_not_flagged(tmp_path):
    # The empty element split() yields after a terminating "\n" must not count.
    (tmp_path / "a.txt").write_text("clean\n")
    assert hygiene.findings(["a.txt"], tmp_path) == []


def test_missing_final_newline_flagged(tmp_path):
    (tmp_path / "a.txt").write_text("no newline at end")
    assert "a.txt: missing final newline" in hygiene.findings(["a.txt"], tmp_path)


def test_final_newline_present_clean(tmp_path):
    (tmp_path / "a.txt").write_text("has newline\n")
    assert hygiene.findings(["a.txt"], tmp_path) == []


def test_empty_file_no_final_newline_finding(tmp_path):
    (tmp_path / "e.txt").write_bytes(b"")
    assert hygiene.findings(["e.txt"], tmp_path) == []


def test_conflict_markers_flagged(tmp_path):
    body = "a\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> feature\nb\n"
    (tmp_path / "c.txt").write_text(body)
    fs = hygiene.findings(["c.txt"], tmp_path)
    assert "c.txt:2: merge conflict marker" in fs
    assert "c.txt:4: merge conflict marker" in fs
    assert "c.txt:6: merge conflict marker" in fs


def test_diff3_base_marker_flagged(tmp_path):
    body = "a\n<<<<<<< HEAD\nx\n||||||| base\nz\n=======\ny\n>>>>>>> feat\n"
    (tmp_path / "c.txt").write_text(body)
    fs = hygiene.findings(["c.txt"], tmp_path)
    assert "c.txt:4: merge conflict marker" in fs


def test_setext_and_short_equals_not_conflict(tmp_path):
    # False-positive guard: a Markdown setext underline of other widths and a
    # short REPL prompt must NOT be flagged; only a bare seven-equals line and
    # the seven-char angle/pipe-plus-space markers are conflict markers.
    body = "Title\n=====\n\nsub\n=========\n\n>>> repl()\n"
    (tmp_path / "d.md").write_text(body)
    fs = hygiene.findings(["d.md"], tmp_path)
    assert [f for f in fs if "merge conflict marker" in f] == []


def test_invalid_yaml_flagged(tmp_path):
    (tmp_path / "bad.yml").write_text("foo: [1, 2\nbar: baz\n")
    fs = hygiene.findings(["bad.yml"], tmp_path)
    assert any("bad.yml" in f and "invalid YAML" in f for f in fs)


def test_valid_yaml_clean(tmp_path):
    (tmp_path / "ok.yaml").write_text("foo: 1\nbar: [1, 2]\n")
    assert hygiene.findings(["ok.yaml"], tmp_path) == []


def test_both_yaml_extensions_checked(tmp_path):
    # Plan says *.yml; the removed check-yaml hook and this repo also carry
    # *.yaml (e.g. .pre-commit-config.yaml, opengrep rules), so both are parsed.
    (tmp_path / "a.yml").write_text("x: [1\n")
    (tmp_path / "b.yaml").write_text("y: {1\n")
    fs = hygiene.findings(["a.yml", "b.yaml"], tmp_path)
    assert any("a.yml" in f and "invalid YAML" in f for f in fs)
    assert any("b.yaml" in f and "invalid YAML" in f for f in fs)


def test_yaml_check_only_for_yaml_ext(tmp_path):
    # Same broken content in a non-yaml file is not parsed as YAML.
    (tmp_path / "notyaml.txt").write_text("foo: [1, 2\n")
    fs = hygiene.findings(["notyaml.txt"], tmp_path)
    assert [f for f in fs if "invalid YAML" in f] == []


def test_binary_file_skipped(tmp_path):
    # A NUL byte marks binary content; text hygiene does not apply even though
    # the bytes carry a trailing space and no final newline.
    (tmp_path / "b.bin").write_bytes(b"trailing \x00\x00no newline ")
    assert hygiene.findings(["b.bin"], tmp_path) == []


def test_missing_path_skipped(tmp_path):
    # A tracked path absent from the worktree is skipped, not a hard error.
    assert hygiene.findings(["ghost.txt"], tmp_path) == []


def test_symlink_skipped(tmp_path):
    (tmp_path / "real.txt").write_text("fine\n")
    (tmp_path / "link.txt").symlink_to("real.txt")
    assert hygiene.findings(["real.txt", "link.txt"], tmp_path) == []


def test_repo_tree_is_hygiene_clean():
    # Self-hosting lock: tackbox's own tracked tree must pass its own hygiene.
    fs = hygiene.findings(hygiene.tracked_files(ROOT), ROOT)
    assert fs == [], "hygiene findings in tracked tree:\n" + "\n".join(fs)


def test_main_returns_zero_on_clean_repo():
    # main() drives tracked_files + findings over the real repo; green tree.
    assert hygiene.main() == 0

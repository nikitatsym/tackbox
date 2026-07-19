"""`tackbox hook`: PostToolUse lint + worktree-wide approvals consistency, and
the PreToolUse manifest-approval gate.

Drives `python -m tackbox.cli hook` with Claude Code hook-event JSON on stdin
and pins exit code / stdout / stderr for each contract case. The hook derives
the repo from the event's `cwd`, not the process cwd, so every case runs the
subprocess from TACKBOX_ROOT (a git repo with no dev.py - guard fails there)
and points `cwd` at the fixture instead.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import commit_all, git, init_repo, tackbox_env

from tackbox.cli import _finding_line, _partition_findings, _span_lines
from tackbox.engines import Finding, active_engines, lintable

TACKBOX_ROOT = Path(__file__).resolve().parents[2]

GO_MOD = "module fixture\n\ngo 1.21\n"

GO_ERC001 = """package pkg

import "errors"

func Fail() error {
\terr := errors.New("x")
\tif err != nil {
\t\t_ = "swallowed"
\t}
\treturn errors.New("noop")
}
"""

GO_CLEAN = """package pkg

func Two() int {
\treturn 2
}
"""

GO_BROKEN = """package pkg

func F() int {
\treturn undefinedThing
}
"""

# ERC001 on line 7 (Fail); Clean() below is edited on a line the finding is not on.
GO_ERC001_PLUS_CLEAN = """package pkg

import "errors"

func Fail() error {
\terr := errors.New("x")
\tif err != nil {
\t\t_ = "swallowed"
\t}
\treturn errors.New("noop")
}

func Clean() int {
\treturn 3
}
"""

# Two ERC001 branches: A on line 7, B on line 15. Distinct bodies so an edit can
# span exactly one.
GO_TWO_ERC001 = """package pkg

import "errors"

func A() error {
\terr := errors.New("x")
\tif err != nil {
\t\t_ = "swallowed"
\t}
\treturn errors.New("noop")
}

func B() error {
\terr := errors.New("y")
\tif err != nil {
\t\t_ = "swallowed"
\t}
\treturn errors.New("noop")
}
"""


def _init(root: Path) -> None:
    init_repo(root, commit=True)


def _hook(event: dict) -> subprocess.CompletedProcess:
    return _hook_raw(json.dumps(event))


def _hook_raw(stdin: str) -> subprocess.CompletedProcess:
    # cwd = TACKBOX_ROOT on purpose: it is a git repo WITHOUT dev.py, so any
    # accidental reliance on the process cwd (instead of the event's cwd) makes
    # the guard fail and the PostToolUse-lint cases below drop to exit 0.
    return subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "hook"],
        input=stdin,
        cwd=TACKBOX_ROOT,
        env=tackbox_env(),
        capture_output=True,
        text=True,
    )


def _dev_py(root: Path) -> None:
    (root / "dev.py").write_text("# stub dev.py so the hook guard fires\n")


# -- PostToolUse ----------------------------------------------------------


def test_post_go_erc001_exit2_stderr(tmp_path):
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "bad.go").write_text(GO_ERC001)
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {"file_path": str(tmp_path / "pkg" / "bad.go")},
        }
    )
    assert r.returncode == 2, f"findings must block with exit 2:\n{r.stdout}\n{r.stderr}"
    # No new_string in the event -> whole-file scope; the erclint finding blocks.
    assert "pkg/bad.go:7" in r.stderr and "errcheck" in r.stderr, r.stderr
    # The one-line explanation rides along; the invariant, never a marker recipe.
    assert "ERC001" in r.stderr and "err-branch must propagate" in r.stderr, r.stderr
    assert "no-report" not in r.stderr, f"marker recipe leaked into hook output:\n{r.stderr}"
    # Other direction of the compile-break contract: a compiling package that
    # merely has a finding is not reported as a compile break.
    assert "does not compile" not in r.stderr, r.stderr
    assert r.stdout == "", f"nothing on stdout in PostToolUse:\n{r.stdout}"


def test_post_go_compile_break_exit2(tmp_path):
    # A non-compiling package blocks with a readable one-line contract (exit 2,
    # no JSON dump); the pkg / pkg.test variants dedup to a single line.
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "bad.go").write_text(GO_BROKEN)
    (tmp_path / "pkg" / "bad_test.go").write_text(
        'package pkg\n\nimport "testing"\n\nfunc TestF(t *testing.T) { _ = F() }\n'
    )
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / "pkg" / "bad.go"),
                "new_string": "\treturn undefinedThing",
            },
        }
    )
    assert r.returncode == 2, f"compile break must block with exit 2:\n{r.stdout}\n{r.stderr}"
    assert "package fixture/pkg does not compile" in r.stderr, r.stderr
    assert "undefinedThing" in r.stderr, f"first compile error must be shown:\n{r.stderr}"
    assert r.stderr.count("does not compile") == 1, f"pkg/pkg.test dedup to one line:\n{r.stderr}"
    assert r.stdout == "" and "{" not in r.stderr, f"no JSON dump on a compile break:\n{r.stdout}\n{r.stderr}"


def test_post_go_clean_exit0(tmp_path):
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "ok.go").write_text(GO_CLEAN)
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "cwd": str(tmp_path),
            "tool_input": {"file_path": str(tmp_path / "pkg" / "ok.go")},
        }
    )
    assert r.returncode == 0, f"clean file must exit 0:\n{r.stdout}\n{r.stderr}"
    assert "ERC001" not in r.stderr, r.stderr


def test_post_non_edit_tool_noop(tmp_path):
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "cwd": str(tmp_path),
            "tool_input": {"command": "ls"},
        }
    )
    assert r.returncode == 0, f"non-edit tool must be a no-op:\n{r.stdout}\n{r.stderr}"
    assert r.stdout == "" and r.stderr == ""


def test_post_file_outside_source_set_exit0(tmp_path):
    # A gitignored file carrying a real ERC001 violation must NOT be linted:
    # it is outside the source set, so the hook is a no-op (exit 0). Adversarial
    # - proves the guard is the source set, not "any .go path handed to us".
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / ".gitignore").write_text("ignored/\n")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "bad.go").write_text(GO_ERC001)
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {"file_path": str(tmp_path / "ignored" / "bad.go")},
        }
    )
    assert r.returncode == 0, f"out-of-source-set file must exit 0:\n{r.stdout}\n{r.stderr}"
    assert "ERC001" not in r.stderr, r.stderr


def test_post_cwd_outside_git_exit0(tmp_path):
    r = _hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),  # tmp_path is not a git repo
            "tool_input": {"file_path": str(tmp_path / "x.go")},
        }
    )
    assert r.returncode == 0, f"non-git cwd must be a no-op:\n{r.stdout}\n{r.stderr}"
    assert r.stdout == "" and r.stderr == ""


def test_post_git_without_devpy_exit0(tmp_path):
    # A real violation, but no dev.py at the repo root -> guard no-op. Adversarial
    # against the guard: the hook must stay silent where it is not wired in.
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "bad.go").write_text(GO_ERC001)
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {"file_path": str(tmp_path / "pkg" / "bad.go")},
        }
    )
    assert r.returncode == 0, f"no dev.py -> guard no-op:\n{r.stdout}\n{r.stderr}"
    assert "ERC001" not in r.stderr, r.stderr


def test_broken_stdin_exit1(tmp_path):
    r = _hook_raw("this is not json {")
    assert r.returncode == 1, f"broken stdin must exit 1 (non-blocking):\n{r.stdout}\n{r.stderr}"
    assert r.stderr.strip() != "", "one stderr line expected"
    assert "Traceback" not in r.stderr, r.stderr


# -- PostToolUse diff-scope -----------------------------------------------


def test_post_edit_clean_line_pre_existing_elsewhere_silent(tmp_path):
    # Diff-scope: editing a clean line (Clean's return) while an ERC001 sits on
    # line 7 (Fail) must NOT block. The clean path is fully silent - at exit 0
    # PostToolUse output never reaches the model, so a heads-up line has no home.
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "bad.go").write_text(GO_ERC001_PLUS_CLEAN)
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / "pkg" / "bad.go"),
                "old_string": "\treturn 2",
                "new_string": "\treturn 3",
            },
        }
    )
    assert r.returncode == 0, f"pre-existing finding elsewhere must not block:\n{r.stderr}"
    assert r.stdout == "" and r.stderr == "", f"clean path is fully silent:\n{r.stdout!r}\n{r.stderr!r}"


def test_post_edit_finding_line_blocks_and_summarizes_pre_existing(tmp_path):
    # Editing A's err-branch (line 7) blocks on A's finding; B's finding (line 15)
    # is pre-existing elsewhere - summarized in one line, not itemized.
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "bad.go").write_text(GO_TWO_ERC001)
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / "pkg" / "bad.go"),
                "old_string": "x",
                "new_string": '\terr := errors.New("x")\n\tif err != nil {',
            },
        }
    )
    assert r.returncode == 2, f"a finding on the edited line must block:\n{r.stderr}"
    assert "pkg/bad.go:7" in r.stderr and "errcheck" in r.stderr, r.stderr
    assert "1 pre-existing elsewhere" in r.stderr, r.stderr
    assert "pkg/bad.go:15" not in r.stderr, f"pre-existing is summarized, not itemized:\n{r.stderr}"


def test_span_lines_multiline_and_repeat():
    # A repeated substring counts every occurrence (over-report), and a two-line
    # substring spans both its lines.
    assert _span_lines("a\nX\nb\nX\nc\n", ["X"]) == {2, 4}
    assert _span_lines("p\nq\nr\n", ["q\nr"]) == {2, 3}


def test_partition_location_unknown_over_reports():
    unknown = Finding(rule="opengrep-json-unparseable", file=None, line=None)
    on, els = _partition_findings([unknown], "a.go", {1})
    assert on == [unknown] and els == []


def test_partition_scopes_by_file_and_line():
    on_diff = Finding(rule="errcheck", file="a.go", line=7)
    off_line = Finding(rule="errcheck", file="a.go", line=99)
    other_file = Finding(rule="errcheck", file="b.go", line=7)
    on, els = _partition_findings([on_diff, off_line, other_file], "a.go", {7})
    assert on == [on_diff]
    assert els == [off_line, other_file]


def test_partition_whole_file_when_affected_none():
    f = Finding(rule="errcheck", file="a.go", line=7)
    on, els = _partition_findings([f], "a.go", None)
    assert on == [f] and els == []


def test_finding_line_with_message_collapses_whitespace():
    f = Finding(rule="errcheck", file="a.go", line=7, message="must  propagate\n or capture")
    assert _finding_line(f) == "a.go:7: errcheck: must propagate or capture"


def test_finding_line_without_message_keeps_old_format():
    f = Finding(rule="errcheck", file="a.go", line=7)
    assert _finding_line(f) == "a.go:7: errcheck"


def test_finding_line_location_unknown():
    f = Finding(rule="r", file=None, line=None, message="m")
    assert _finding_line(f) == "?: r: m"


# -- PreToolUse: approval-manifest gate -----------------------------------


def _ask(r: subprocess.CompletedProcess) -> dict:
    assert r.returncode == 0, f"ask decision still exits 0:\n{r.stdout}\n{r.stderr}"
    payload = json.loads(r.stdout)
    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "ask", payload
    return out


def _pre_write(tmp_path: Path, rel: str, content: str) -> subprocess.CompletedProcess:
    return _hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "cwd": str(tmp_path),
            "tool_input": {"file_path": str(tmp_path / rel), "content": content},
        }
    )


def _pre_edit(tmp_path: Path, rel: str, old: str, new: str) -> subprocess.CompletedProcess:
    return _hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / rel),
                "old_string": old,
                "new_string": new,
            },
        }
    )


# The two canonical ask texts (plan, user-approved) - fixed verbatim.
CANON_SINGLE = (
    "approve suppression marker: "
    "app/svc.py#Handler.process: no-report: legacy path, covered upstream"
)
CANON_MULTI = (
    "approve 4 suppression markers (Allow = all, Deny = none;"
    " re-add one by one to decide individually):\n"
    "  app/svc.py#Handler.process: no-report: legacy path, covered upstream\n"
    "  app/svc.py#Handler.retry: no-report: transient, retried x2\n"
    "  lib/util.ts#parseCfg: parse-skip: config validated upstream"
)


def test_pre_manifest_single_entry_ask(tmp_path):
    # Adding one manifest line draws the canonical single ask, quoting the entry.
    _dev_py(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    _init(tmp_path)
    r = _pre_write(
        tmp_path,
        ".tackbox/approvals",
        "app/svc.py#Handler.process: no-report: legacy path, covered upstream\n",
    )
    assert _ask(r)["permissionDecisionReason"] == CANON_SINGLE


def test_pre_manifest_multi_entry_one_ask(tmp_path):
    # A single edit adding several entries draws ONE atomic ask listing every one
    # (Allow = all, Deny = none). Duplicates collapse to ` x<count>`; the header
    # counts total occurrences (here 1 + 2 + 1 = 4). Deny is atomic - the whole
    # edit is rejected and nothing lands; re-add one by one to decide individually.
    _dev_py(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    _init(tmp_path)
    r = _pre_write(
        tmp_path,
        ".tackbox/approvals",
        "app/svc.py#Handler.process: no-report: legacy path, covered upstream\n"
        "app/svc.py#Handler.retry: no-report: transient, retried\n"
        "app/svc.py#Handler.retry: no-report: transient, retried\n"
        "lib/util.ts#parseCfg: parse-skip: config validated upstream\n",
    )
    out = _ask(r)
    assert out["permissionDecisionReason"] == CANON_MULTI
    # ONE ask, not several: the whole payload is a single JSON object on one line.
    assert r.stdout.strip().count("\n") == 0, f"expected one ask object:\n{r.stdout}"


def test_pre_manifest_edit_adds_line_ask(tmp_path):
    # The arm covers Edit as well as Write: an Edit that appends one entry asks.
    _dev_py(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox" / "approvals").write_text("a.py: no-report: one\n")
    _init(tmp_path)
    r = _pre_edit(
        tmp_path,
        ".tackbox/approvals",
        "a.py: no-report: one",
        "a.py: no-report: one\nc.py: no-report: three",
    )
    assert (
        _ask(r)["permissionDecisionReason"]
        == "approve suppression marker: c.py: no-report: three"
    )


def test_pre_manifest_removal_free(tmp_path):
    # Removing a manifest line is free - no ask.
    _dev_py(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox" / "approvals").write_text(
        "a.py: no-report: one\nb.py: no-report: two\n"
    )
    _init(tmp_path)
    r = _pre_write(tmp_path, ".tackbox/approvals", "a.py: no-report: one\n")
    assert r.returncode == 0 and r.stdout == "", f"removing a line is free:\n{r.stdout}"


def test_pre_manifest_nested_not_gated(tmp_path):
    # Root-only: a .tackbox/approvals under a subdirectory is not the manifest;
    # adding an entry line to it draws no ask (same-named files elsewhere do not
    # participate).
    _dev_py(tmp_path)
    (tmp_path / "sub" / ".tackbox").mkdir(parents=True)
    _init(tmp_path)
    r = _pre_write(
        tmp_path,
        "sub/.tackbox/approvals",
        "app/x.py: no-report: nested, not the root manifest\n",
    )
    assert r.returncode == 0 and r.stdout == "", f"nested manifest is not gated:\n{r.stdout}"


# -- PreToolUse: reporters ask (unchanged arm) ----------------------------


def test_pre_reporters_add_line_ask(tmp_path):
    _dev_py(tmp_path)
    (tmp_path / ".tackbox-reporters").write_text("a.go#f: sink one\n")
    _init(tmp_path)
    r = _pre_write(
        tmp_path, ".tackbox-reporters", "a.go#f: sink one\nb.go#g: sink two\n"
    )
    assert "b.go#g" in _ask(r)["permissionDecisionReason"]


def test_pre_reporters_remove_line_allow(tmp_path):
    _dev_py(tmp_path)
    (tmp_path / ".tackbox-reporters").write_text("a.go#f: sink one\nb.go#g: sink two\n")
    _init(tmp_path)
    r = _pre_write(tmp_path, ".tackbox-reporters", "a.go#f: sink one\n")
    assert r.returncode == 0 and r.stdout == "", f"removing a declaration is free:\n{r.stdout}"


# -- PreToolUse: code markers no longer ask (the old _marker_gate is gone) -


def test_pre_code_marker_edit_no_ask(tmp_path):
    # Marker edits in code files no longer draw a Pre ask. Approval rides the
    # manifest now, not the code edit; an unapproved code marker surfaces at the
    # next Post consistency event instead.
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _pre_edit(
        tmp_path,
        "app/svc.py",
        "x = 1",
        "# no-report: added straight into code\nx = 1",
    )
    assert r.returncode == 0 and r.stdout == "", f"a code marker edit must not ask:\n{r.stdout}"


def test_pre_code_marker_write_no_ask(tmp_path):
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _pre_write(tmp_path, "new.py", "# parse-skip: generated blob\nx = 1\n")
    assert r.returncode == 0 and r.stdout == "", f"a new file with a marker must not ask:\n{r.stdout}"


def test_pre_plain_edit_allow(tmp_path):
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _pre_edit(tmp_path, "svc.py", "a := 1", "a := 2")
    assert r.returncode == 0 and r.stdout == "", f"a plain edit is free:\n{r.stdout}"


# -- PostToolUse: worktree-wide approvals consistency (Edit/Write + Bash) --


def _bash(tmp_path: Path) -> subprocess.CompletedProcess:
    return _hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "cwd": str(tmp_path),
            "tool_input": {"command": "true"},
        }
    )


def _post_edit(
    tmp_path: Path, rel: str, old: str = "x = 1", new: str = "x = 2"
) -> subprocess.CompletedProcess:
    return _hook(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / rel),
                "old_string": old,
                "new_string": new,
            },
        }
    )


def _block(r: subprocess.CompletedProcess) -> str:
    # The Bash consistency arm: a hit rides the top-level block JSON, exit 0.
    assert r.returncode == 0, f"block decision still exits 0:\n{r.stdout}\n{r.stderr}"
    payload = json.loads(r.stdout)
    assert payload["decision"] == "block", payload
    # The payload is the canonical block texts alone - no lint-section header.
    assert "approvals (whole tree):" not in payload["reason"], payload
    return payload["reason"]


def _block_edit(r: subprocess.CompletedProcess) -> str:
    # The edit-tool consistency arm reports as the lint arm does: block lines on
    # stderr, exit 2 - not the Bash arm's JSON decision.
    assert r.returncode == 2, f"an edit-tool hit exits 2:\n{r.stdout}\n{r.stderr}"
    assert r.stdout == "", f"an edit-tool hit prints no JSON:\n{r.stdout}"
    assert "approvals (whole tree):" not in r.stderr, r.stderr
    return r.stderr


def test_post_edit_unapproved_marker_blocks(tmp_path):
    _dev_py(tmp_path)
    (tmp_path / "svc.py").write_text("# no-report: unapproved planted marker\nx = 1\n")
    _init(tmp_path)
    reason = _block_edit(_post_edit(tmp_path, "svc.py"))
    assert "Unapproved suppression marker" in reason, reason
    assert "svc.py: no-report: unapproved planted marker" in reason, reason


def test_post_edit_cross_file_inconsistency_blocks(tmp_path):
    # ADVERSARIAL: an inconsistency planted in one file blocks the next edit of an
    # unrelated, clean file - the check is worktree-wide, not scoped to the edited
    # path. b.py has no finding of its own; a.py's uncovered marker is the block.
    _dev_py(tmp_path)
    (tmp_path / "a.py").write_text("# no-report: planted in a, never approved\nx = 1\n")
    (tmp_path / "b.py").write_text("y = 1\n")
    _init(tmp_path)
    reason = _block_edit(_post_edit(tmp_path, "b.py", old="y = 1", new="y = 2"))
    assert "Unapproved suppression marker" in reason, reason
    assert "a.py: no-report: planted in a, never approved" in reason, reason


def _shelled_repo(tmp_path: Path) -> None:
    # A marker planted by a shell command (not the Edit hook): the file is clean at
    # commit, then a marker is written straight to the worktree.
    _dev_py(tmp_path)
    (tmp_path / "svc.py").write_text("x = 1\n")
    _init(tmp_path)
    (tmp_path / "svc.py").write_text("# no-report: shelled in at module scope\nx = 1\n")


def test_bash_shelled_marker_blocks(tmp_path):
    _shelled_repo(tmp_path)
    reason = _block(_bash(tmp_path))
    assert "Unapproved suppression marker" in reason, reason
    assert "svc.py: no-report: shelled in at module scope" in reason, reason


def test_bash_shelled_marker_repeats(tmp_path):
    # Stateless: a second event repeats the same block (no snooze, no pairing).
    _shelled_repo(tmp_path)
    _block(_bash(tmp_path))
    reason = _block(_bash(tmp_path))
    assert "svc.py: no-report: shelled in at module scope" in reason, reason


def test_bash_shelled_marker_silenced_by_manifest(tmp_path):
    # A covering manifest line makes the tree consistent immediately - silent even
    # uncommitted.
    _shelled_repo(tmp_path)
    _block(_bash(tmp_path))
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox" / "approvals").write_text(
        "svc.py: no-report: shelled in at module scope\n"
    )
    r = _bash(tmp_path)
    assert r.returncode == 0 and r.stdout == "", f"a covering line silences it:\n{r.stdout}"


def test_bash_shelled_marker_silenced_by_reversion(tmp_path):
    _shelled_repo(tmp_path)
    _block(_bash(tmp_path))
    (tmp_path / "svc.py").write_text("x = 1\n")
    r = _bash(tmp_path)
    assert r.returncode == 0 and r.stdout == "", f"reverting the marker silences it:\n{r.stdout}"


def test_post_orphan_after_marker_removal_blocks(tmp_path):
    # A manifest entry outliving its marker is an orphan - red until the line goes.
    _dev_py(tmp_path)
    (tmp_path / "svc.py").write_text("x = 1\n")
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox" / "approvals").write_text(
        "svc.py: no-report: this marker was removed\n"
    )
    _init(tmp_path)
    reason = _block_edit(_post_edit(tmp_path, "svc.py"))
    assert "Orphaned approval" in reason, reason
    assert "svc.py: no-report: this marker was removed" in reason, reason


def test_committed_unapproved_marker_still_blocks(tmp_path):
    # Tree-shaped: committing an unapproved marker does not approve it. It stays red
    # on every later event (the wall survives commit / --no-verify).
    _dev_py(tmp_path)
    (tmp_path / "svc.py").write_text("# no-report: committed but never approved\nx = 1\n")
    _init(tmp_path)
    reason = _block(_bash(tmp_path))
    assert "Unapproved suppression marker" in reason, reason
    assert "svc.py: no-report: committed but never approved" in reason, reason


def test_bash_covered_branch_silent(tmp_path):
    # Approvals travel with the tree: a branch whose marker is covered by its own
    # committed manifest is silent after checkout - no state, no HEAD diff.
    _dev_py(tmp_path)
    (tmp_path / "base.txt").write_text("base\n")
    _init(tmp_path)
    git(tmp_path, "checkout", "-q", "-b", "feature")
    (tmp_path / "svc.py").write_text("# no-report: covered on this branch\nx = 1\n")
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox" / "approvals").write_text(
        "svc.py: no-report: covered on this branch\n"
    )
    commit_all(tmp_path, "feature")
    git(tmp_path, "checkout", "-q", "main")
    git(tmp_path, "checkout", "-q", "feature")
    r = _bash(tmp_path)
    assert r.returncode == 0 and r.stdout == "", f"a covered branch is silent:\n{r.stdout}"


def test_bash_unborn_head_marker_blocks(tmp_path):
    # Worktree-based, not HEAD-based: an unborn HEAD (git init, no commit) with a
    # shelled-in untracked marker still blocks - no `git show HEAD` needed.
    _dev_py(tmp_path)
    init_repo(tmp_path, commit=False)
    (tmp_path / "svc.py").write_text("# no-report: marker in an unborn-head repo\nx = 1\n")
    reason = _block(_bash(tmp_path))
    assert "Unapproved suppression marker" in reason, reason
    assert "svc.py: no-report: marker in an unborn-head repo" in reason, reason


def test_bash_clean_tree_silent(tmp_path):
    _dev_py(tmp_path)
    (tmp_path / "svc.py").write_text("x = 1\n")
    _init(tmp_path)
    r = _bash(tmp_path)
    assert r.returncode == 0 and r.stdout == "", f"a clean tree is silent:\n{r.stdout}"


def test_pre_manifest_multiedit_one_ask(tmp_path):
    # The manifest arm covers MultiEdit: several edits adding entry lines still
    # draw ONE atomic ask listing every added entry.
    _dev_py(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "MultiEdit",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / ".tackbox" / "approvals"),
                "edits": [
                    {"old_string": "", "new_string": "a.py: no-report: first entry line"},
                    {"old_string": "", "new_string": "b.py: no-report: second entry line"},
                ],
            },
        }
    )
    assert _ask(r)["permissionDecisionReason"] == (
        "approve 2 suppression markers (Allow = all, Deny = none;"
        " re-add one by one to decide individually):\n"
        "  a.py: no-report: first entry line\n"
        "  b.py: no-report: second entry line"
    )


def test_bash_unresolvable_file_blocks(tmp_path):
    # A marker-bearing file that does not parse refuses resolution and blocks
    # with the canonical unresolvable text - pinned verbatim (plan, user-approved).
    # Java, not Python: the python grammar recovers from most damage without an
    # ERROR node, while a missing brace is a guaranteed java ERROR.
    _dev_py(tmp_path)
    (tmp_path / "Bad.java").write_text(
        "class C {\n"
        "  void a() {\n"
        "    if (true) {\n"
        "      x(); // no-report: marker in a broken file\n"
        "    // MISSING closing brace\n"
        "  }\n"
        "  void b() { y(); }\n"
        "}\n"
    )
    _init(tmp_path)
    reason = _block(_bash(tmp_path))
    assert (
        "Unresolvable file (syntax does not parse; its markers and approvals are "
        "unverified - fix the syntax first):" in reason
    ), reason
    assert "  Bad.java" in reason, reason


def test_bash_lang_marker_needs_entry(tmp_path):
    # The markdown lang marker is part of the inventory: shelled in, it blocks
    # until covered; its entry text runs through the comment's closing `-->`.
    _dev_py(tmp_path)
    (tmp_path / "notes.md").write_text("plain\n")
    _init(tmp_path)
    (tmp_path / "notes.md").write_text("<!-- tackbox: lang=ru personal repo -->\nplain\n")
    reason = _block(_bash(tmp_path))
    assert "Unapproved suppression marker" in reason, reason
    assert "notes.md: tackbox: lang=ru personal repo -->" in reason, reason
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox" / "approvals").write_text(
        "notes.md: tackbox: lang=ru personal repo -->\n"
    )
    r = _bash(tmp_path)
    assert r.returncode == 0 and r.stdout == "", f"a covering entry silences it:\n{r.stdout}"


def test_bash_marker_in_unlintable_txt_silent(tmp_path):
    # D012: only markers in files an engine would lint participate. A marker in a
    # dead .py.txt is inert - no block.
    _dev_py(tmp_path)
    (tmp_path / "fixture.py.txt").write_text(
        "x = 1  # no-report: dead marker in an unlintable file\n"
    )
    _init(tmp_path)
    r = _bash(tmp_path)
    assert r.returncode == 0 and r.stdout == "", f"an unlintable-file marker is silent:\n{r.stdout}"


def test_lintable_normalizes_backslash_paths():
    # Windows-shape sanity (D012): a backslash hook path meets the same
    # forward-slash path filters git output uses; git-side paths stay '/'.
    eng = active_engines()
    assert lintable("go\\analyzers\\x\\testdata\\src\\x\\x.go", eng) is False
    assert lintable("app\\svc.py", eng) is True

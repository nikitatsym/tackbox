"""Step C acceptance: `tackbox hook` PostToolUse lint + PreToolUse marker gate.

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

from conftest import init_repo, tackbox_env

from tackbox.cli import _partition_findings, _span_lines
from tackbox.engines import Finding

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


# -- PreToolUse -----------------------------------------------------------


def _ask(r: subprocess.CompletedProcess) -> dict:
    assert r.returncode == 0, f"ask decision still exits 0:\n{r.stdout}\n{r.stderr}"
    payload = json.loads(r.stdout)
    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "ask", payload
    return out


def _pre_edit(tmp_path: Path, old: str, new: str) -> subprocess.CompletedProcess:
    """PreToolUse Edit of svc.go from old_string to new_string in a fresh
    dev-guarded repo - the marker gate's canonical input."""
    _dev_py(tmp_path)
    _init(tmp_path)
    return _hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / "svc.go"),
                "old_string": old,
                "new_string": new,
            },
        }
    )


def test_pre_introduce_marker_ask(tmp_path):
    r = _pre_edit(tmp_path, "x := 1", "// no-report: bootstrap only\nx := 1")
    assert "no-report" in _ask(r)["permissionDecisionReason"]


def test_pre_remove_marker_allow(tmp_path):
    r = _pre_edit(tmp_path, "// no-report: bootstrap only\nx := 1", "x := 1")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "", f"removing a marker is free (no output):\n{r.stdout}"


def test_pre_change_reason_ask(tmp_path):
    r = _pre_edit(tmp_path, "// no-report: old reason\nx := 1", "// no-report: new reason\nx := 1")
    assert "no-report" in _ask(r)["permissionDecisionReason"]


def test_pre_introduce_test_skip_marker_ask(tmp_path):
    r = _pre_edit(tmp_path, "x := 1", "// test-skip: flaky under race\nx := 1")
    assert "test-skip" in _ask(r)["permissionDecisionReason"]


def test_pre_remove_test_skip_marker_allow(tmp_path):
    r = _pre_edit(tmp_path, "// test-skip: flaky under race\nx := 1", "x := 1")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "", f"removing a marker is free (no output):\n{r.stdout}"


def test_pre_write_new_file_with_marker_ask(tmp_path):
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / "new.go"),
                "content": "package pkg\n// parse-skip: generated blob\nvar x = 1\n",
            },
        }
    )
    assert "parse-skip" in _ask(r)["permissionDecisionReason"]


def test_pre_reporters_add_line_ask(tmp_path):
    _dev_py(tmp_path)
    (tmp_path / ".tackbox-reporters").write_text("a.go#f: sink one\n")
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / ".tackbox-reporters"),
                "content": "a.go#f: sink one\nb.go#g: sink two\n",
            },
        }
    )
    assert "b.go#g" in _ask(r)["permissionDecisionReason"]


def test_pre_reporters_remove_line_allow(tmp_path):
    _dev_py(tmp_path)
    (tmp_path / ".tackbox-reporters").write_text("a.go#f: sink one\nb.go#g: sink two\n")
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / ".tackbox-reporters"),
                "content": "a.go#f: sink one\n",
            },
        }
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == "", f"removing a declaration line is free:\n{r.stdout}"


def test_pre_plain_edit_no_marker_allow(tmp_path):
    r = _pre_edit(tmp_path, "a := 1", "a := 2")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "", f"a plain edit is free:\n{r.stdout}"

"""Step C acceptance: `tackbox hook` PostToolUse lint + PreToolUse marker gate.

Drives `python -m tackbox.cli hook` with Claude Code hook-event JSON on stdin
and pins exit code / stdout / stderr for each contract case. The hook derives
the repo from the event's `cwd`, not the process cwd, so every case runs the
subprocess from TACKBOX_ROOT (a git repo with no dev.py - guard fails there)
and points `cwd` at the fixture instead.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

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


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(TACKBOX_ROOT / "py")
    return env


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
        env=_env(),
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
    assert "ERC001" in r.stderr, f"findings go to stderr:\n{r.stderr}"
    assert r.stdout == "", f"nothing on stdout in PostToolUse:\n{r.stdout}"


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


# -- PreToolUse -----------------------------------------------------------


def _ask(r: subprocess.CompletedProcess) -> dict:
    assert r.returncode == 0, f"ask decision still exits 0:\n{r.stdout}\n{r.stderr}"
    payload = json.loads(r.stdout)
    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "ask", payload
    return out


def test_pre_introduce_marker_ask(tmp_path):
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / "svc.go"),
                "old_string": "x := 1",
                "new_string": "// no-sentry: bootstrap only\nx := 1",
            },
        }
    )
    assert "no-sentry" in _ask(r)["permissionDecisionReason"]


def test_pre_remove_marker_allow(tmp_path):
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / "svc.go"),
                "old_string": "// no-sentry: bootstrap only\nx := 1",
                "new_string": "x := 1",
            },
        }
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == "", f"removing a marker is free (no output):\n{r.stdout}"


def test_pre_change_reason_ask(tmp_path):
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / "svc.go"),
                "old_string": "// no-sentry: old reason\nx := 1",
                "new_string": "// no-sentry: new reason\nx := 1",
            },
        }
    )
    assert "no-sentry" in _ask(r)["permissionDecisionReason"]


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
    _dev_py(tmp_path)
    _init(tmp_path)
    r = _hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / "svc.go"),
                "old_string": "a := 1",
                "new_string": "a := 2",
            },
        }
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == "", f"a plain edit is free:\n{r.stdout}"

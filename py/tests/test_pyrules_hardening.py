"""Step PYE hardening: the full `tackbox lint` invocation neutralizes flake8's
ambient suppression channels (noqa, consumer config). Run through the subprocess,
not a direct flake8 call, so they pin our invocation - drop --disable-noqa or
--isolated from _pyrules_argv and one goes red. The select-gate (a consumer's own
plugin shadowing TBX codes) is pinned structurally in test_engines.py. Fixtures
are inline so self-lint never sees the planted violations.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TACKBOX_ROOT = Path(__file__).resolve().parents[2]

# Swallowed exception; `# noqa` sits on the `try:` line - the line pyrules
# reports the finding on - so a noqa-respecting run would suppress it.
SWALLOW_NOQA = """def h():
    try:  # noqa
        work()
    except ValueError as e:
        pass
"""

SWALLOW = """def h():
    try:
        work()
    except ValueError as e:
        pass
"""


def _init(root: Path) -> None:
    def g(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    g("add", ".")
    g("commit", "-q", "-m", "fixture")


def _lint(root: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(TACKBOX_ROOT / "py")
    return subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "lint", ".", "--no-cache"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )


def test_noqa_on_the_flagged_line_does_not_suppress(tmp_path):
    # `# noqa` is banned as a class; our --disable-noqa must ignore it.
    (tmp_path / "h.py").write_text(SWALLOW_NOQA)
    _init(tmp_path)
    r = _lint(tmp_path)
    assert "python-swallowed-exception" in r.stdout, f"noqa wrongly suppressed:\n{r.stdout}\n{r.stderr}"
    assert r.returncode == 1, f"{r.stdout}\n{r.stderr}"


def test_consumer_setup_cfg_ignore_does_not_suppress(tmp_path):
    # A consumer setup.cfg that extend-ignores our code; our --isolated must skip it.
    (tmp_path / "h.py").write_text(SWALLOW)
    (tmp_path / "setup.cfg").write_text("[flake8]\nextend-ignore = TBX001\n")
    _init(tmp_path)
    r = _lint(tmp_path)
    assert "python-swallowed-exception" in r.stdout, f"consumer config wrongly suppressed:\n{r.stdout}\n{r.stderr}"
    assert r.returncode == 1, f"{r.stdout}\n{r.stderr}"

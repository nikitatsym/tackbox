"""Fixtures for the root dev.py orchestrator (dev-script contract).

dev.py is a thin dispatcher over lint / test / e2e / check; these pin the
contract (e2e no-op, check = lint + test with both always run and failures
surfaced, usage exit code) without re-running the whole suite. The real
end-to-end proof is `python dev.py check` in acceptance.
"""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dev():
    spec = importlib.util.spec_from_file_location("devscript", ROOT / "dev.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dev = _load_dev()


def test_e2e_is_noop():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = dev.e2e()
    assert rc == 0
    assert "no e2e tests found" in buf.getvalue()


def test_check_runs_lint_then_test(monkeypatch):
    calls = []
    monkeypatch.setattr(dev, "lint", lambda: calls.append("lint") or 0)
    monkeypatch.setattr(dev, "test", lambda: calls.append("test") or 0)
    assert dev.check() == 0
    assert calls == ["lint", "test"]


def test_check_surfaces_lint_failure(monkeypatch):
    monkeypatch.setattr(dev, "lint", lambda: 1)
    monkeypatch.setattr(dev, "test", lambda: 0)
    assert dev.check() != 0


def test_check_runs_test_even_when_lint_fails(monkeypatch):
    # check aggregates: a lint failure must not skip the test suite, and the
    # aggregate exit is non-zero. Attacks the "check swallows a red lint" bug.
    calls = []
    monkeypatch.setattr(dev, "lint", lambda: calls.append("lint") or 1)
    monkeypatch.setattr(dev, "test", lambda: calls.append("test") or 0)
    assert dev.check() != 0
    assert calls == ["lint", "test"]


def test_unknown_command_exits_2():
    r = subprocess.run(
        [sys.executable, str(ROOT / "dev.py"), "bogus"], capture_output=True, text=True
    )
    assert r.returncode == 2
    assert "usage" in r.stderr


def test_no_command_exits_2():
    r = subprocess.run(
        [sys.executable, str(ROOT / "dev.py")], capture_output=True, text=True
    )
    assert r.returncode == 2

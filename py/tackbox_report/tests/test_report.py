"""Unit tests for the tackbox_report first-cut capture helper.

Covers the spec-required behaviours: empty-DSN no-op, log-before-drop invariant,
60s rate-limit drop, per-name task fingerprint, and concurrency isolation.
"""

from __future__ import annotations

import logging
import threading

import pytest

import tackbox_report as report
from conftest import records


# --------------------------------------------------------------------------
# init: empty DSN = log-only no-op
# --------------------------------------------------------------------------
def test_init_empty_dsn_is_log_only_noop(log):
    report._reset_for_test()
    report.init("", logger=log)
    assert report.is_ready() is False

    # A capture before/without init must not raise and must still log locally.
    report.report_error("boom", RuntimeError("x"), {"a": "b"}, "area.suffix")
    assert report.is_ready() is False
    assert any(r.getMessage() == "boom" for r in records(log))


def test_init_empty_dsn_silent_missing(log):
    report._reset_for_test()
    report.init("", logger=log, silent_missing=True)
    assert records(log) == []  # no WARN line


def test_dsn_from_env(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("GLITCHTIP_DSN", raising=False)
    assert report.dsn_from_env() == ""
    monkeypatch.setenv("GLITCHTIP_DSN", "https://k@h/2")
    assert report.dsn_from_env() == "https://k@h/2"
    monkeypatch.setenv("SENTRY_DSN", "https://k@h/1")
    assert report.dsn_from_env() == "https://k@h/1"  # SENTRY_DSN wins


# --------------------------------------------------------------------------
# capture core: level, fingerprint, tags, msg context
# --------------------------------------------------------------------------
def test_report_error_shape(events):
    report.report_error("db down", ValueError("no conn"), {"db": "main"}, "vault.save")
    assert len(events) == 1
    e = events[0]
    assert e["level"] == "error"
    assert e["fingerprint"] == ["vault.save"]
    assert e["tags"]["db"] == "main"
    assert e["contexts"]["tackbox"]["msg"] == "db down"


def test_report_warn_is_warning_level(events):
    report.report_warn("cache miss", RuntimeError("x"), None, "cache.write")
    assert events[0]["level"] == "warning"
    assert events[0]["fingerprint"] == ["cache.write"]


def test_report_panic_fingerprint_and_fatal(events):
    report.report_panic("ipc-loop", RuntimeError("kaboom"))
    assert len(events) == 1
    assert events[0]["level"] == "fatal"
    assert events[0]["fingerprint"] == ["panic:ipc-loop"]
    assert events[0]["tags"]["source"] == "ipc-loop"


def test_report_panic_non_exception_value(events):
    report.report_panic("worker", "string-panic")
    assert events[0]["fingerprint"] == ["panic:worker"]


# --------------------------------------------------------------------------
# log-before-drop invariant
# --------------------------------------------------------------------------
def test_log_runs_before_rate_limit_drop(events, log):
    report.report_error("first", None, None, "area.dup")
    report.report_error("second", None, None, "area.dup")  # dropped by rate limit
    # Only one event shipped, but BOTH lines were logged (invariant).
    assert len(events) == 1
    logged = [r.getMessage() for r in records(log) if r.levelno == logging.ERROR]
    assert "first" in logged and "second" in logged


def test_panic_logs_at_fatal_level(events, log):
    report.report_panic("t", RuntimeError("x"))
    fatal = [r for r in records(log) if r.levelname == "FATAL"]
    assert len(fatal) == 1
    assert fatal[0].getMessage() == "panic in t"


# --------------------------------------------------------------------------
# rate limit
# --------------------------------------------------------------------------
def test_rate_limit_drops_repeat_within_window(events):
    for _ in range(5):
        report.report_error("dup", None, None, "area.rl")
    assert len(events) == 1  # 4 repeats dropped inside the 60s window


def test_distinct_keys_not_limited(events):
    report.report_error("a", None, None, "area.a")
    report.report_error("b", None, None, "area.b")
    assert len(events) == 2


def test_empty_dedup_key_never_dropped(events):
    report.report_error("x", None, None, "")
    report.report_error("x", None, None, "")
    assert len(events) == 2  # empty key opts out of rate limiting


def test_rate_limit_reopens_after_window(events, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(report.time, "monotonic", lambda: clock["t"])
    report.report_error("dup", None, None, "area.win")
    report.report_error("dup", None, None, "area.win")
    assert len(events) == 1
    clock["t"] += 61.0  # advance past the 60s window
    report.report_error("dup", None, None, "area.win")
    assert len(events) == 2


# --------------------------------------------------------------------------
# background-task wrapper (GoSafe analog) - per-name fingerprint task:<name>
# --------------------------------------------------------------------------
def test_run_task_raised_exception_task_fingerprint(events):
    def boom():
        raise ValueError("in task")

    report.run_task("importer", boom, join=True)
    assert len(events) == 1
    assert events[0]["fingerprint"] == ["task:importer"]
    assert events[0]["tags"]["task"] == "importer"


def test_run_task_returned_exception_task_fingerprint(events):
    # mirrors Go func() error: a returned error is captured, not raised.
    report.run_task("syncer", lambda: RuntimeError("returned"), join=True)
    assert len(events) == 1
    assert events[0]["fingerprint"] == ["task:syncer"]


def test_run_task_success_captures_nothing(events):
    report.run_task("ok-task", lambda: None, join=True)
    assert events == []


def test_run_task_per_name_independent_fingerprints(events):
    report.run_task("alpha", lambda: RuntimeError("a"), join=True)
    report.run_task("beta", lambda: RuntimeError("b"), join=True)
    fps = sorted(e["fingerprint"][0] for e in events)
    assert fps == ["task:alpha", "task:beta"]


# --------------------------------------------------------------------------
# concurrency smoke: no scope/fingerprint bleed across threads (D003 analog)
# --------------------------------------------------------------------------
def test_concurrent_captures_no_scope_bleed(events):
    n = 24
    barrier = threading.Barrier(n)

    def worker(i: int) -> None:
        barrier.wait()  # maximise simultaneity
        report.report_error(
            f"msg-{i}", RuntimeError(str(i)), {"worker": f"w{i}"}, f"area.k{i}"
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(events) == n
    # Each event's fingerprint must match its own worker tag - no bleed.
    for e in events:
        worker_id = e["tags"]["worker"]  # "w<i>"
        assert e["fingerprint"] == [f"area.k{worker_id[1:]}"]


def test_concurrent_run_tasks_per_name_fingerprints(events):
    n = 16
    threads = [
        report.run_task(f"task{i}", lambda: RuntimeError("x"))
        for i in range(n)
    ]
    for t in threads:
        t.join()
    fps = sorted(e["fingerprint"][0] for e in events)
    assert fps == sorted(f"task:task{i}" for i in range(n))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

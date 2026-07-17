"""Unit tests for the tackbox_report capture helper.

Covers empty-DSN no-op, log-before-drop invariant, 60s rate-limit drop, panic
fingerprints, the user lane, and concurrency isolation.
"""

from __future__ import annotations

import logging
import threading

import pytest

import tackbox_report as report
from conftest import records


# --------------------------------------------------------------------------
# public surface: reporting verbs only
# --------------------------------------------------------------------------
def test_public_api_is_reporting_only():
    assert report.__all__ == [
        "ReportError",
        "Notice",
        "init",
        "dsn_from_env",
        "is_ready",
        "verify",
        "flush",
        "report_error",
        "report_warn",
        "report_quiet",
        "notify",
        "report_panic",
        "set_notifier",
        "crumb",
    ]


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
# concurrency smoke: no scope/fingerprint bleed across threads (D003)
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


# --------------------------------------------------------------------------
# user lane: report* -> notifier (always); quiet -> none; notify -> no capture
# --------------------------------------------------------------------------
@pytest.fixture
def notices():
    """Record every Notice the registered notifier receives; clear on teardown."""
    got: list[report.Notice] = []
    report.set_notifier(got.append)
    yield got
    report.set_notifier(None)


def test_report_error_dispatches_user_lane_when_not_ready(log, notices):
    report._reset_for_test()  # leaves the fixture's notifier in place
    report.init("", logger=log, silent_missing=True)
    assert report.is_ready() is False
    report.report_error("connection lost mid-stream", RuntimeError("boom"), {"area": "net"}, "net.conn")
    assert len(notices) == 1  # user lane delivers even with capture disabled
    assert notices[0].level == "error"
    assert notices[0].dedup_key == "net.conn"


def test_report_error_dispatches_user_lane_when_rate_limited(events, notices):
    report.report_error("poll failed on stale token", RuntimeError("e1"), None, "poll.stale")
    report.report_error("poll failed on stale token", RuntimeError("e2"), None, "poll.stale")
    assert len(events) == 1  # duplicate capture dropped within the window
    assert len(notices) == 2  # every event reaches the user lane


def test_quiet_captures_warning_no_user_lane(events, notices):
    report.report_quiet("cache refresh degraded, using stale", RuntimeError("timeout"), None, "cache.refresh")
    assert len(events) == 1
    assert events[0]["level"] == "warning"
    assert events[0]["fingerprint"] == ["cache.refresh"]
    assert notices == []  # quiet never touches the user lane


def test_notify_user_lane_only_does_not_consume_rate_slot(events, notices):
    report.notify("you appear to be offline", RuntimeError("net down"), None, "conn.offline")
    assert len(notices) == 1
    assert notices[0].level == "notice"
    assert events == []  # notify captures nothing
    # Same dedup_key: proves notify consumed no capture rate slot.
    report.report_error("still offline after retry", RuntimeError("net down"), None, "conn.offline")
    assert len(events) == 1
    assert len(notices) == 2


def test_report_panic_dispatches_fatal_notice(events, notices):
    report.report_panic("ipc-loop", RuntimeError("kaboom"))
    assert len(events) == 1
    assert notices[0].level == "fatal"
    assert notices[0].dedup_key == "panic:ipc-loop"
    assert notices[0].msg == "panic in ipc-loop"


def test_notifier_exception_does_not_break_caller(events):
    def boom_notifier(_notice):
        raise RuntimeError("notifier is broken")

    report.set_notifier(boom_notifier)
    try:
        # Returns normally: a propagating notifier error would raise here.
        report.report_error("upload failed mid-flight", RuntimeError("hangup"), None, "upload.fail")
    finally:
        report.set_notifier(None)
    fps = {tuple(e["fingerprint"]) for e in events}
    assert ("upload.fail",) in fps  # original event still captured
    assert ("report.notifier",) in fps  # broken notifier captured telemetry-only


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

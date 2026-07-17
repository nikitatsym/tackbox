"""Direct error-reporting helpers for Python applications using Sentry or GlitchTip.

Shared runtime contract: https://github.com/nikitatsym/tackbox/blob/main/docs/report-contracts.md
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlparse

import sentry_sdk

__all__ = [
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

# ---------------------------------------------------------------------------
# A FATAL level above CRITICAL for panic logs. addLevelName only adds a name
# for 60; it does not rename CRITICAL.
# ---------------------------------------------------------------------------
_LEVEL_FATAL = logging.CRITICAL + 10  # 60
logging.addLevelName(_LEVEL_FATAL, "FATAL")

# Sentry level strings. The error/warning/fatal strings double as user-lane
# notice levels; _NOTICE_NOTICE is the user-lane-only level notify() carries.
_SENTRY_ERROR = "error"
_SENTRY_WARNING = "warning"
_SENTRY_FATAL = "fatal"
_SENTRY_INFO = "info"
_NOTICE_NOTICE = "notice"


class ReportError(Exception):
    """Raised when ``init`` or ``verify`` cannot start capture."""


@dataclass(frozen=True)
class Notice:
    """One user-lane event handed to the registered notifier. The app owns
    rendering and any coalescing keyed on ``dedup_key``; the helper never
    suppresses the user lane."""

    msg: str
    level: str
    dedup_key: str
    cause: Optional[BaseException] = None
    tags: Mapping[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Module state. Guarded by _lock for the rate-limit map; scalars are set once
# at init and only read afterward.
# ---------------------------------------------------------------------------
_ready = False
_rate_window = 60.0  # seconds
_flush_timeout = 2.0  # seconds
_last_sent: dict[str, float] = {}
_lock = threading.Lock()
_notifier: Optional[Callable[[Notice], None]] = None


class _StructFormatter(logging.Formatter):
    """Renders the local sink line as ``<level> <msg>`` plus err/tags, so the
    full context survives in log-only mode. dedup_key is deliberately absent:
    it routes the Sentry event, it is not diagnostics."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extra = getattr(record, "tackbox", None)
        if extra:
            parts = []
            if extra.get("err") is not None:
                parts.append(f"err={extra['err']!r}")
            if extra.get("tags"):
                parts.append(f"tags={extra['tags']}")
            if parts:
                base = f"{base} {' '.join(parts)}"
        return base


def _default_logger() -> logging.Logger:
    lg = logging.getLogger("tackbox.report")
    if not lg.handlers:
        h = logging.StreamHandler()
        h.setFormatter(_StructFormatter("%(asctime)s %(levelname)s report: %(message)s"))
        lg.addHandler(h)
        lg.setLevel(logging.DEBUG)
        lg.propagate = False  # own sink; do not double-log through root
    return lg


_logger: logging.Logger = _default_logger()


# ---------------------------------------------------------------------------
# Init / lifecycle
# ---------------------------------------------------------------------------
def init(
    dsn: str = "",
    release: Optional[str] = None,
    environment: Optional[str] = None,
    *,
    verify: bool = False,
    verify_timeout: float = 3.0,
    rate_window: float = 60.0,
    flush_timeout: float = 2.0,
    debug: bool = False,
    silent_missing: bool = False,
    logger: Optional[logging.Logger] = None,
    **sentry_options: Any,
) -> None:
    """Initialize capture. Empty ``dsn`` -> log-only no-op (safe before init).

    Raises ``ReportError`` when ``verify`` is set and the healthcheck cannot be
    shipped. Extra ``**sentry_options`` are forwarded verbatim to
    ``sentry_sdk.init`` (e.g. ``before_send``, ``transport``, ``sample_rate``)
    -- the Pythonic escape hatch, also used by the tests.
    """
    global _ready, _rate_window, _flush_timeout, _logger

    if logger is not None:
        _logger = logger

    if not dsn:
        if not silent_missing:
            _logger.warning(
                "DSN unset, capture disabled, running log-only "
                "(set SENTRY_DSN or GLITCHTIP_DSN)"
            )
        _ready = False
        return

    # default_integrations=False: this helper owns capture and grouping, so the
    # LoggingIntegration must not turn our own log-before-drop lines into a
    # second, un-rate-limited event.
    sentry_sdk.init(
        dsn=dsn,
        release=release,
        environment=environment,
        debug=debug,
        default_integrations=False,
        integrations=sentry_options.pop("integrations", []),
        **sentry_options,
    )
    _ready = True
    if rate_window > 0:
        _rate_window = rate_window
    if flush_timeout > 0:
        _flush_timeout = flush_timeout

    if verify:
        _verify(verify_timeout)
        _logger.info("capture flushed (delivery unconfirmed), DSN=%s", _mask_dsn(dsn))
        return
    _logger.info("capture enabled (unverified), DSN=%s", _mask_dsn(dsn))


def dsn_from_env() -> str:
    """SENTRY_DSN, then GLITCHTIP_DSN; empty string if neither is set."""
    return os.environ.get("SENTRY_DSN") or os.environ.get("GLITCHTIP_DSN") or ""


def is_ready() -> bool:
    return _ready


def verify(timeout: float = 3.0) -> None:
    """Ship one healthcheck event (fingerprint ``report.startup``) and flush.

    Best-effort: ``sentry_sdk.flush`` returns ``None``, so this cannot detect a
    delivery timeout. It raises only when called before a successful ``init``.
    """
    _verify(timeout)


def _verify(timeout: float) -> None:
    if not _ready:
        raise ReportError("verify: not initialized")
    with sentry_sdk.new_scope() as scope:
        scope.set_level(_SENTRY_INFO)
        scope.fingerprint = ["report.startup"]
        scope.set_tag("healthcheck", "true")
        sentry_sdk.capture_message("report.Verify")
    sentry_sdk.flush(timeout)


def flush(timeout: Optional[float] = None) -> None:
    if not _ready:
        return
    sentry_sdk.flush(timeout if timeout is not None else _flush_timeout)


# ---------------------------------------------------------------------------
# User lane
# ---------------------------------------------------------------------------
def set_notifier(fn: Optional[Callable[[Notice], None]]) -> None:
    """Register the user-lane sink; ``None`` clears it. With no notifier the user
    lane is a no-op (the local log and capture still run). The callback runs on
    the caller's thread; the app bridges to its UI thread itself."""
    global _notifier
    with _lock:
        _notifier = fn


def _dispatch_notice(notice: Notice) -> None:
    with _lock:
        fn = _notifier
    if fn is None:
        return
    try:
        fn(notice)
    except Exception as exc:
        # A throwing notifier must not break the caller's path or recurse into
        # the user lane; capture it telemetry-only (the quiet lane).
        report_quiet("report notifier failed", exc, None, "report.notifier")


# ---------------------------------------------------------------------------
# Capture core
# ---------------------------------------------------------------------------
def report_error(
    msg: str,
    cause: Optional[BaseException] = None,
    tags: Optional[Mapping[str, str]] = None,
    dedup_key: str = "",
) -> None:
    """Level error: an unrecoverable failure handled here. Local log + user lane
    + capture. dedup_key is the rate-limit bucket and the Sentry fingerprint."""
    _emit(_SENTRY_ERROR, logging.ERROR, _SENTRY_ERROR, msg, cause, tags, dedup_key)


def report_warn(
    msg: str,
    cause: Optional[BaseException] = None,
    tags: Optional[Mapping[str, str]] = None,
    dedup_key: str = "",
) -> None:
    """Level warning: a transient or external fault you recovered from. Local log
    + user lane + capture."""
    _emit(_SENTRY_WARNING, logging.WARNING, _SENTRY_WARNING, msg, cause, tags, dedup_key)


def report_quiet(
    msg: str,
    cause: Optional[BaseException] = None,
    tags: Optional[Mapping[str, str]] = None,
    dedup_key: str = "",
) -> None:
    """Capture without the user lane: local log + warning-level capture, no
    notice. For background / self-healed / degraded-with-fallback failures where
    anything error-severe would deserve user visibility."""
    _emit(_SENTRY_WARNING, logging.WARNING, "", msg, cause, tags, dedup_key)


def notify(
    msg: str,
    cause: Optional[BaseException] = None,
    tags: Optional[Mapping[str, str]] = None,
    dedup_key: str = "",
) -> None:
    """Feed only the user lane: local log + a 'notice'-level notice, no capture
    and no rate-limit state touched, so a following report_error/report_warn with
    the same dedup_key still captures. For an expected environmental fault (the
    user lost connectivity). cause is the caught error the notice is about."""
    _log_at(logging.WARNING, msg, cause, tags)
    _dispatch_notice(_notice(_NOTICE_NOTICE, msg, cause, tags, dedup_key))


def _emit(
    sentry_level: str,
    log_level: int,
    notice_level: str,
    msg: str,
    cause: Optional[BaseException],
    tags: Optional[Mapping[str, str]],
    dedup_key: str,
) -> None:
    # Local log first; user lane (D005) dispatched unconditionally before the
    # readiness/rate-limit gate when notice_level is set; capture last, gated -
    # so a rate-limited or log-only event still leaves a local record.
    _log_at(log_level, msg, cause, tags)
    if notice_level:
        _dispatch_notice(_notice(notice_level, msg, cause, tags, dedup_key))
    if not _ready or _should_drop(dedup_key):
        return
    _capture(sentry_level, msg, cause, tags, dedup_key)


def _notice(
    level: str,
    msg: str,
    cause: Optional[BaseException],
    tags: Optional[Mapping[str, str]],
    dedup_key: str,
) -> Notice:
    return Notice(msg=msg, level=level, dedup_key=dedup_key, cause=cause,
                  tags=dict(tags) if tags else {})


def report_panic(name: str, recovered: Any) -> None:
    """Level fatal. Fingerprint ``panic:<name>``. Pass the caught exception
    (or any recovered value)."""
    key = f"panic:{name}"
    exc = recovered if isinstance(recovered, BaseException) else None
    _log_at(
        _LEVEL_FATAL,
        f"panic in {name}",
        recovered,
        {"source": name},
        exc_info=exc,
    )
    _dispatch_notice(_notice(_SENTRY_FATAL, f"panic in {name}", exc, {"source": name}, key))
    if not _ready or _should_drop(key):
        return
    with sentry_sdk.new_scope() as scope:
        scope.set_level(_SENTRY_FATAL)
        scope.set_tag("source", name)
        scope.fingerprint = [key]
        sentry_sdk.capture_exception(
            exc if exc is not None else Exception(f"panic in {name}: {recovered!r}")
        )


def crumb(
    category: str, message: str, data: Optional[Mapping[str, Any]] = None
) -> None:
    """A breadcrumb toward the next capture; not itself an event and no local
    line."""
    if not _ready:
        return
    sentry_sdk.add_breadcrumb(
        category=category,
        message=message,
        data=dict(data) if data else {},
        level=_SENTRY_INFO,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _capture(
    sentry_level: str,
    msg: str,
    cause: Optional[BaseException],
    tags: Optional[Mapping[str, str]],
    dedup_key: str,
) -> None:
    with sentry_sdk.new_scope() as scope:
        scope.set_level(sentry_level)
        if dedup_key:
            scope.fingerprint = [dedup_key]
        for k, v in (tags or {}).items():
            scope.set_tag(k, str(v))
        scope.set_context("tackbox", {"msg": msg})
        if isinstance(cause, BaseException):
            sentry_sdk.capture_exception(cause)
        else:
            sentry_sdk.capture_exception(Exception(msg if cause is None else str(cause)))


def _log_at(
    level: int,
    msg: str,
    cause: Optional[Any],
    tags: Optional[Mapping[str, str]],
    *,
    exc_info: Optional[BaseException] = None,
) -> None:
    extra: dict[str, Any] = {}
    if cause is not None:
        extra["err"] = str(cause)
    if tags:
        extra["tags"] = dict(sorted(tags.items()))
    _logger.log(level, msg, extra={"tackbox": extra}, exc_info=exc_info)


def _should_drop(key: str) -> bool:
    """In-memory 60s rate limit keyed by dedup_key. Empty key is never limited
    (lets Sentry auto-group). Thread-safe."""
    if not key:
        return False
    now = time.monotonic()
    with _lock:
        prev = _last_sent.get(key)
        if prev is not None and now - prev < _rate_window:
            return True
        _last_sent[key] = now
    return False


def _mask_dsn(dsn: str) -> str:
    """Host+path for logs, dropping the secret key. urlparse yields an empty
    hostname on a malformed string rather than raising, so no guard is needed."""
    u = urlparse(dsn)
    if not u.hostname:
        return "<malformed>"
    return f"{u.hostname}{u.path}"


def _reset_for_test() -> None:
    """Test-only: clear rate-limit state and readiness between cases."""
    global _ready, _rate_window, _flush_timeout
    with _lock:
        _last_sent.clear()
    _ready = False
    _rate_window = 60.0
    _flush_timeout = 2.0

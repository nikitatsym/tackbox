"""tackbox_report: Python runtime capture helper (FIRST CUT, review only).

A thin wrapper over ``sentry-sdk`` that mirrors the Go ``go/report`` and JS
``js/report.js`` helpers and the error-reporting spec. An empty DSN makes every
call a log-only no-op, so a repo can adopt the API (and satisfy a future linter)
before any Glitchtip endpoint exists.

Semantics carried over from go/report:

* Log-before-drop invariant: every ``report_error`` / ``report_warn`` /
  ``report_panic`` writes one structured local line *before* the readiness and
  rate-limit checks, so nothing is lost in log-only mode.
* In-memory 60s rate limit keyed by ``dedup_key`` (``_should_drop``); the same
  key is the Sentry fingerprint (grouping).
* Per-name fingerprints (DECISIONS.md D002): ``panic:<name>`` for the panic
  analog, ``task:<name>`` for the background-task wrapper. Built directly from
  the name so differently-named failures group and rate-limit independently.
* Concurrency-isolated capture (DECISIONS.md D003): every capture site runs
  inside ``sentry_sdk.new_scope()`` (forks the current scope), and the
  background-task wrapper additionally forks a per-thread
  ``sentry_sdk.isolation_scope()`` -- the sentry-sdk 2.x analog of Go's
  ``sentry.CurrentHub().Clone()`` per goroutine. Concurrent captures cannot
  bleed fingerprint/tags into one another.

NOT wired into publishing/CI/pyproject. NOT the pyrules linter. See DESIGN.md
for the load-bearing forks.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any, Callable, Coroutine, Mapping, Optional
from urllib.parse import urlparse

import sentry_sdk

__all__ = [
    "ReportError",
    "init",
    "dsn_from_env",
    "is_ready",
    "verify",
    "flush",
    "report_error",
    "report_warn",
    "report_panic",
    "crumb",
    "run_task",
    "run_task_async",
]

# ---------------------------------------------------------------------------
# Levels: a FATAL level above CRITICAL, mirroring Go's levelFatal = ERROR+4.
# addLevelName only *adds* a name for 60; it does not rename CRITICAL.
# ---------------------------------------------------------------------------
_LEVEL_FATAL = logging.CRITICAL + 10  # 60
logging.addLevelName(_LEVEL_FATAL, "FATAL")

# Sentry level strings.
_SENTRY_ERROR = "error"
_SENTRY_WARNING = "warning"
_SENTRY_FATAL = "fatal"
_SENTRY_INFO = "info"


class ReportError(Exception):
    """An ``init`` / ``verify`` failure (Pythonic analog of Go's returned error)."""


# ---------------------------------------------------------------------------
# Module state. Guarded by _lock for the rate-limit map; scalars are set once
# at init and only read afterward.
# ---------------------------------------------------------------------------
_ready = False
_rate_window = 60.0  # seconds
_flush_timeout = 2.0  # seconds
_last_sent: dict[str, float] = {}
_lock = threading.Lock()


class _StructFormatter(logging.Formatter):
    """Renders the local sink line as ``<level> <msg>`` plus err/tags, so the
    full context survives in log-only mode. dedup_key is deliberately absent:
    it routes the Sentry event, it is not diagnostics (parity with go/report)."""

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

    Mirrors ``report.Init``. Raises ``ReportError`` when ``verify`` is set and
    the healthcheck cannot be shipped. Extra ``**sentry_options`` are forwarded
    verbatim to ``sentry_sdk.init`` (e.g. ``before_send``, ``transport``,
    ``sample_rate``) -- the Pythonic escape hatch, also used by the tests.
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

    # default_integrations=False mirrors js/report.js: this helper owns capture
    # and grouping, so the LoggingIntegration must not turn our own
    # log-before-drop lines into a second, un-rate-limited event.
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
        _logger.info("capture verified, DSN=%s", _mask_dsn(dsn))
        return
    _logger.info("capture enabled (unverified), DSN=%s", _mask_dsn(dsn))


def dsn_from_env() -> str:
    """SENTRY_DSN, then GLITCHTIP_DSN; empty string if neither is set."""
    return os.environ.get("SENTRY_DSN") or os.environ.get("GLITCHTIP_DSN") or ""


def is_ready() -> bool:
    return _ready


def verify(timeout: float = 3.0) -> None:
    """Ship one healthcheck event (fingerprint ``report.startup``) and flush.

    Best-effort: the Python ``sentry_sdk.flush`` returns ``None`` (unlike Go's
    bool), so this cannot detect a delivery timeout the way ``report.Verify``
    does. It raises only when called before a successful ``init``.
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
# Capture core
# ---------------------------------------------------------------------------
def report_error(
    msg: str,
    cause: Optional[BaseException] = None,
    tags: Optional[Mapping[str, str]] = None,
    dedup_key: str = "",
) -> None:
    """Level error: an unrecoverable failure handled here. dedup_key is the
    rate-limit bucket and the Sentry fingerprint."""
    _emit(_SENTRY_ERROR, logging.ERROR, msg, cause, tags, dedup_key)


def report_warn(
    msg: str,
    cause: Optional[BaseException] = None,
    tags: Optional[Mapping[str, str]] = None,
    dedup_key: str = "",
) -> None:
    """Level warning: a transient or external fault you recovered from."""
    _emit(_SENTRY_WARNING, logging.WARNING, msg, cause, tags, dedup_key)


def _emit(
    sentry_level: str,
    log_level: int,
    msg: str,
    cause: Optional[BaseException],
    tags: Optional[Mapping[str, str]],
    dedup_key: str,
) -> None:
    # Local log BEFORE the readiness/rate-limit drop (the invariant): a
    # rate-limited or log-only event still leaves a local record.
    _log_at(log_level, msg, cause, tags)
    if not _ready or _should_drop(dedup_key):
        return
    _capture(sentry_level, msg, cause, tags, dedup_key)


def report_panic(name: str, recovered: Any) -> None:
    """Level fatal panic analog. Fingerprint ``panic:<name>`` (D002). Pass the
    caught exception (or any recovered value)."""
    key = f"panic:{name}"
    exc = recovered if isinstance(recovered, BaseException) else None
    _log_at(
        _LEVEL_FATAL,
        f"panic in {name}",
        recovered,
        {"source": name},
        exc_info=exc,
    )
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
    line (parity with go/report Crumb)."""
    if not _ready:
        return
    sentry_sdk.add_breadcrumb(
        category=category,
        message=message,
        data=dict(data) if data else {},
        level=_SENTRY_INFO,
    )


# ---------------------------------------------------------------------------
# Background-task wrapper (GoSafe analog): threading + asyncio (see DESIGN.md #1)
# ---------------------------------------------------------------------------
def run_task(
    name: str,
    fn: Callable[[], Optional[BaseException]],
    *,
    daemon: bool = False,
    join: bool = False,
) -> threading.Thread:
    """Run ``fn`` in a background thread under capture (GoSafe analog).

    A raised exception OR a returned exception (mirroring Go's ``func() error``)
    is captured under the per-name fingerprint ``task:<name>`` (mirror of Go's
    ``go.task:<name>``), rate-limited per name. The whole run is wrapped in a
    per-thread ``sentry_sdk.isolation_scope()`` -- the D003 analog of Go's
    per-goroutine hub clone. Returns the ``Thread`` so the caller may ``join``.
    """

    def _run() -> None:
        with sentry_sdk.isolation_scope():
            try:
                result = fn()
            except Exception as exc:
                report_error("background task failed", cause=exc,
                             tags={"task": name}, dedup_key=f"task:{name}")
                return
            if isinstance(result, BaseException):
                _report_task_failure(name, result)

    t = threading.Thread(target=_run, name=f"tackbox-task:{name}", daemon=daemon)
    t.start()
    if join:
        t.join()
    return t


def run_task_async(
    name: str,
    coro: Coroutine[Any, Any, Optional[BaseException]],
) -> asyncio.Task[None]:
    """Run ``coro`` as a background asyncio task under capture (asyncio analog of
    run_task; the async arm of Go's GoSafe intent).

    A raised exception OR a returned exception (mirroring Go's ``func() error``)
    is captured under the per-name fingerprint ``task:<name>``, rate-limited per
    name, log-before-drop preserved -- identical routing to run_task. The
    coroutine runs inside a per-task ``sentry_sdk.isolation_scope()`` fork: each
    asyncio task runs in its own copied context, so this fork isolates the task's
    scope (D003) the way the per-thread fork does for run_task -- concurrent
    tasks cannot bleed scope/fingerprint into one another.

    Scheduled fire-and-forget via ``asyncio.create_task``; the returned
    ``asyncio.Task`` is the join analog (``await`` it to wait). A failure is
    captured, never re-raised, so awaiting mirrors ``run_task(..., join=True)``.
    An ``asyncio.CancelledError`` propagates (it is not a task failure). Must be
    called from within a running event loop.
    """

    async def _run() -> None:
        with sentry_sdk.isolation_scope():
            try:
                result = await coro
            except Exception as exc:
                report_error("background task failed", cause=exc,
                             tags={"task": name}, dedup_key=f"task:{name}")
                return
            if isinstance(result, BaseException):
                _report_task_failure(name, result)

    return asyncio.create_task(_run(), name=f"tackbox-task:{name}")


def _report_task_failure(name: str, exc: BaseException) -> None:
    """Capture a background-task failure under the per-name fingerprint
    ``task:<name>``. The ``except`` handlers call ``report_error`` inline (so
    pyrules credits the background boundary by name, no marker); this shares that
    exact routing for the returned-exception path."""
    report_error("background task failed", cause=exc,
                 tags={"task": name}, dedup_key=f"task:{name}")


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
    (lets Sentry auto-group). Thread-safe (parity with Go's sync.Map)."""
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

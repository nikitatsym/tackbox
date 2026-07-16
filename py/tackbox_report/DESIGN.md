# tackbox_report -- design forks

A Python runtime capture helper over `sentry-sdk`, mirroring the Go `go/report`
and JS `js/report.js` helpers and the error-reporting spec. A self-contained
package under `py/tackbox_report/`, published independently of the `py/tackbox`
linter/CLI.

## API (mirrors go/report, Pythonic)

```python
init(dsn="", release=None, environment=None, *, verify=False,
     verify_timeout=3.0, rate_window=60.0, flush_timeout=2.0,
     debug=False, silent_missing=False, logger=None, **sentry_options) -> None
dsn_from_env() -> str          # SENTRY_DSN, then GLITCHTIP_DSN
is_ready() -> bool
verify(timeout=3.0) -> None    # raises ReportError before init
flush(timeout=None) -> None

report_error(msg, cause=None, tags=None, dedup_key="") -> None
report_warn(msg, cause=None, tags=None, dedup_key="") -> None
report_quiet(msg, cause=None, tags=None, dedup_key="") -> None
notify(msg, cause=None, tags=None, dedup_key="") -> None
report_panic(name, recovered) -> None       # fatal, panic:<name>
crumb(category, message, data=None) -> None
set_notifier(fn | None) -> None   # sink for Notice(msg, level, dedup_key, ...)
run_task(name, fn, *, daemon=False, join=False, quiet=False) -> Thread
run_task_async(name, coro, *, quiet=False) -> asyncio.Task
```

Three lanes - local log (always), user lane (`set_notifier`), Sentry capture
(gated). `report_error`/`report_warn` feed all three; `report_quiet` skips the
user lane; `notify` feeds only the user lane (no capture, no rate-limit state
touched); `report_panic` feeds all three by default. A background task's failure
surfaces to the user lane by default; `quiet=True` routes it telemetry-only at
warning. A raised task exception routes to `task:<name>` at error, not a fatal
`panic:<name>` like Go's GoSafe - Python has no panic/error split, so a raised
exception is a normal failure, not a fatal signal. The user lane is dispatched
before the readiness+rate gate and is never
rate-limited (D005); a notifier that raises is caught and captured
telemetry-only, never breaking the caller's path or recursing into the lane.

Invariants carried over verbatim from `go/report`:

- **Empty DSN = log-only no-op.** `init("")` logs one WARN (unless
  `silent_missing`) and leaves `is_ready()` False; every capture still runs its
  local log and returns without shipping. Safe to call before `init`.
- **Log-before-drop.** `report_error` / `report_warn` / `report_panic` /
  `run_task`'s failure path emit one structured local line *before* the
  readiness and rate-limit checks, so nothing is lost in log-only or
  rate-limited mode. `dedup_key` is deliberately not logged (it routes the
  Sentry event; it is not diagnostics).
- **60s in-memory rate limit** keyed by `dedup_key` (`_should_drop`, thread-safe
  via a `Lock`, `time.monotonic`); the same key is the Sentry fingerprint. Empty
  key is never limited.
- **Per-name fingerprints (D002):** `panic:<name>` for `report_panic`,
  `task:<name>` for `run_task` and `run_task_async`, built directly from the
  name.
- **Concurrency-isolated capture (D003):** every capture site runs inside
  `sentry_sdk.new_scope()`; `run_task` additionally forks a per-thread
  `sentry_sdk.isolation_scope()`, and `run_task_async` forks a per-asyncio-task
  `sentry_sdk.isolation_scope()`.

## sentry-sdk version + isolation API

`sentry-sdk` **2.x** replaced the Hub/scope-stack model (`push_scope`, the Go
`CurrentHub().Clone()` idiom) with a forking scope API. On the installed
**2.64.0**:

- `sentry_sdk.new_scope()` -- context manager that forks the *current* scope,
  applies changes, restores on exit. Used at **every capture site** (error /
  warn / panic / verify). This is the direct D003 analog of Go's per-capture
  `hub.WithScope` on a cloned hub: concurrent captures each mutate their own
  forked scope, so fingerprint/tags cannot bleed. (Verified empirically and by
  the `test_concurrent_*` tests: 24 simultaneous captures, each fingerprint
  matches its own tag.)
- `sentry_sdk.isolation_scope()` -- forks the isolation scope *and* the current
  scope. Used at the `run_task` thread boundary, the analog of giving each
  goroutine its own cloned hub. `threading.Thread` does not propagate
  contextvars, so without an explicit fork a spawned thread would capture
  against the process-global scopes; the isolation fork makes the thread's
  breadcrumbs/tags its own.
- **asyncio isolation (`run_task_async`).** In 2.x the scopes live in
  contextvars. `asyncio.create_task` snapshots the current context
  (`contextvars.copy_context()`) at creation, but that copy *shares the same
  Scope objects* with the parent -- so bare tasks that mutate the current /
  isolation scope bleed into siblings. `run_task_async` runs its coroutine
  inside `with sentry_sdk.isolation_scope():`; because each task executes in its
  own copied context, the fork rebinds the isolation-scope contextvar within
  that task only, giving per-task isolation -- the asyncio analog of the
  per-thread fork. This matches what sentry-sdk 2.x documents (its optional
  `AsyncioIntegration` installs a task factory to fork per task); we fork
  explicitly inside the wrapper instead, so a consumer needs no integration
  wiring. Verified empirically on **2.64.0** and by
  `test_concurrent_run_tasks_async_no_scope_bleed`: 16 tasks that each set a tag
  on their isolation scope, yield, then fail -- 16 distinct `task:<name>`
  fingerprints, zero tag bleed. Without the fork the same 16 collapse to one
  fingerprint (the shared scope's last writer).
- `Scope.fingerprint` is a settable **property** in 2.x (`scope.fingerprint =
  [key]`); there is no `set_fingerprint` method. Level/tags/context use
  `set_level` / `set_tag` / `set_context`.
- Default integrations are disabled (`default_integrations=False`), mirroring
  `js/report.js`. This is load-bearing: the default `LoggingIntegration` would
  turn our own log-before-drop lines (which log at ERROR) into a *second*,
  un-rate-limited Sentry event. Disabling defaults keeps this helper the sole
  capture funnel.

---

## Load-bearing forks (defaults + alternatives)

### 1. Background-task (GoSafe) analog: threads vs asyncio

**Chosen default: `threading`.** `run_task(name, fn)` spawns a
`threading.Thread` (the closest analog to Go's `go func(){}()`), runs `fn`
under `isolation_scope()`, and captures failure under `task:<name>`. Returns the
`Thread` so a caller can `join`.

Two sub-forks inside this:

- **Failure routing.** Go's `GoSafe` splits paths: a panic goes to
  `panic:<name>` (fatal), a returned error to `go.task:<name>` (error). The
  wrapper funnels **both** a raised `Exception` and a returned `Exception`
  (the `func() error` analog) into `task:<name>` at level error.
  `report_panic` remains the separate primitive for the `panic:<name>` fatal
  fingerprint.
  - *Alternative (closer to Go):* route a raised exception in `run_task` through
    `report_panic` -> `panic:<name>` fatal, and reserve `task:<name>` for
    returned errors only. Rejected because a raised exception is Python's
    *normal* failure mode (not an exceptional "panic"), so grouping it as fatal
    would over-signal.
- **Concurrency model.** Both models are implemented: `run_task` (threads) and
  `run_task_async` (asyncio). Python has both worlds and a consumer may be
  async-first, so the wrapper is offered for each; failure routing, `task:<name>`
  fingerprint, rate limit, and log-before-drop are identical across the two.
  - **Schedule vs await (asyncio).** `run_task_async` schedules
    **fire-and-forget** via `asyncio.create_task` and returns the
    `asyncio.Task`. A failure is captured once (per-name `task:<name>`,
    log-before-drop, rate limit) and then re-raised inside the task, so the
    `asyncio.Task` carries the exception: `await`-ing it surfaces the failure to
    the awaiter, who is the single caller that observes it. This diverges from
    `run_task`, whose `Thread.join()` returns nothing -- a joined thread cannot
    hand its exception back, but an `asyncio.Task` can, so the async wrapper does
    not swallow on the await path. An unobserved (never-awaited) task still
    captured its failure exactly once. There is no separate await-inline entry
    point: `await report.run_task_async(...)` already is the inline path, so one
    function covers both. An `asyncio.CancelledError` (a `BaseException`, not
    caught by the wrapper's `except Exception`) propagates and is not reported --
    cancellation is not a task failure, and it must reach the awaiter. Must be
    called from within a running event loop; outside one `create_task` raises
    `RuntimeError` (fail loud, no fallback).
  - **daemon default (threads).** `run_task` defaults to `daemon=False` (the
    task and its capture/flush complete) rather than `daemon=True` (the thread
    dies with the process), to avoid losing an in-flight capture.

### 2. Packaging + name

**Finalized as a PyPI-ready distribution.** Distribution **`tackbox-report`**,
import package `tackbox_report`, living at `py/tackbox_report/` with its own
standalone `pyproject.toml`:

- Build backend `setuptools.build_meta` (`setuptools>=68`, `wheel`).
- Complete metadata: `name`, `version`, `description`, `readme`
  (`README.md`, the PyPI long description), `license = MIT`, `authors`,
  `requires-python = ">=3.11"`, `keywords`, `project.urls`, and trove
  `classifiers` (Alpha, MIT, OS-independent, Python 3.11-3.13, Typing :: Typed).
- Runtime dependency **`sentry-sdk>=2.0,<3.0`**. The floor is 2.0 (the forking
  scope API `new_scope` / `isolation_scope` this helper is built on landed in
  2.0); the `<3.0` cap is deliberate, because that exact API is what changed
  across the 1.x -> 2.x major, so D003 isolation is a 2.x contract.
- Ships a PEP 561 `py.typed` marker (the module is fully type-hinted), included
  in the wheel via `tool.setuptools.package-data`.
- **Separate from the `tackbox` linter wheel** and does not depend on it: a repo
  depends on the helper without pulling flake8, and on the linter without
  pulling sentry-sdk. Parallel to how `go/report` is a sub-package of the Go
  module and `js/report.js` a JS file.
  - *Alternative:* fold it into the existing `tackbox` wheel as
    `tackbox.report`. Rejected: it would put `sentry-sdk` on the linter's
    dependency closure (every `uvx tackbox` lint run would resolve sentry), and
    couple runtime-capture releases to linter releases.

`python -m build` produces the sdist + wheel (`twine check`-clean); the
wheel carries `tackbox_report/__init__.py` and `tackbox_report/py.typed`.

#### Publishing

Published to PyPI as `tackbox-report` via a **Trusted Publisher** (OIDC, no
stored token). The CI job (`.github/workflows/publish-report-py.yml`, separate
from the linter's release job, since these are independent distributions)
builds with `python -m build` and uploads via `pypa/gh-action-pypi-publish` on
a `report-py-v*` tag; the published version comes from `pyproject.toml`. Each
helper release is pinned in consumer manifests, so signatures must not break
without a consumer pass. Release runbook: `docs/publishing-helpers.md`.

### 3. Linter recognition (pyrules is NAME-based)

**Flagged limitation.** The Go linter (erclint) credits a capture only when the
callee resolves to the `go/report` **import path** (origin). The Python engine
(pyrules) has no cross-module type info at the flake8/AST layer: per
`py/tackbox/pyrules/reporters.py`, tier-2 reporter recognition is **by function
name**, declared in `.tackbox-reporters` as `<file>#<func>`, and *any* same-named
call from any module counts (subject to argument-flow of the caught error).

Recognition of `tackbox_report` as a reporter:

- Recognition is by **file-local import origin** (DECISIONS D010, see
  `py/tackbox/pyrules/origin.py`): a call counts only when it resolves, through
  the module's own import bindings, to `tackbox_report` -- `from tackbox_report
  import report_error` or `import tackbox_report` (attribute form included).
  pyrules carries the five verbs (`report_error` / `report_warn` / `report_quiet`
  / `report_panic` / `notify`) as the origin target, a **built-in tier-1 set**
  (DECISIONS D004), so a consumer needs no `.tackbox-reporters` entry -- the
  Python analog of the built-in Go origin check. A same-named local def or a
  foreign import is not the verb, so the old name-model false-positive-credit is
  closed. This package self-credits its own top-level verb defs (a
  `tackbox_report` path segment).
- With that recognition in place, the two background-task wrappers carry **no**
  `# no-report:` marker. Each internal `except` calls `report_error` directly --
  identical routing to the old private `_report_task_failure` (per-name
  `task:<name>` fingerprint, log-before-drop, rate-limit) -- so the background
  boundary is a recognized tier-1 capture, not a TBX001 false positive. The
  returned-exception path (`func() error` analog) shares that call through the
  thin `_report_task_failure` wrapper, which keeps the two wrappers under the
  clone threshold.

### 4. Handler / middleware analog (WrapHandler) -- DEFERRED

Go's `WrapHandler` (and the JS `setupGlobalHandlers`) wrap an HTTP handler with
recover+capture. **Not implemented.** The Python analog is a
WSGI/ASGI middleware (or a framework-specific integration) that recovers an
unhandled exception in a request and routes it through `report_panic`
(`panic:http.<name>`). It is out of scope here: it pulls in a web-framework
surface (WSGI vs ASGI vs Starlette/Django/Flask specifics) the helper should
not commit to. `report_panic` is the building block a middleware would call.

### Other choices made

- **`verify` is best-effort.** Go's `report.Verify` returns whether `Flush`
  delivered within the timeout; the Python `sentry_sdk.flush()` returns `None`,
  so this helper cannot detect a delivery timeout the same way. `verify` ships
  the `report.startup` healthcheck and flushes, raising only when called before
  a successful `init`. Detecting delivery failure would need transport
  introspection.
- **`**sentry_options` passthrough.** `init` forwards unknown kwargs to
  `sentry_sdk.init` (`before_send`, `transport`, `sample_rate`, and so on). This
  is the Pythonic escape hatch (Go's `Options` is a fixed struct) and is what
  the tests use to capture events without a network via `before_send`.
- **Local sink.** Uses stdlib `logging` (logger `tackbox.report`, `propagate=
  False`, a `_StructFormatter` that appends `err=`/`tags=`), not slog-style
  JSON. A `logger=` override mirrors Go's `Options.Logger`. Panic logs at a
  custom `FATAL` level (60, above `CRITICAL`), mirroring Go's `levelFatal`.

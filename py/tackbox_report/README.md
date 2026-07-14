# tackbox-report (Python)

Runtime error-capture helper for Python: a thin wrapper over `sentry-sdk` that
mirrors the Go `go/report` and JS `js/report.js` helpers and the tackbox
error-reporting contract. An empty DSN makes every call a log-only no-op, so a
repo can adopt the API before any Glitchtip/Sentry endpoint exists.

This is one runtime arm of tackbox. The static half - the linter that forces
you to call these helpers - ships separately as the `tackbox` distribution;
installing this helper does not pull the linter, and vice versa.

## Install

```sh
pip install tackbox-report
```

Distribution `tackbox-report`, import package `tackbox_report`. Requires
Python 3.11+ and `sentry-sdk` 2.x.

## Use

```python
import tackbox_report as report

report.init(report.dsn_from_env())           # empty DSN -> log-only no-op
report.set_notifier(on_notice)               # user-lane sink; None clears it

report.report_error("db write failed", cause=exc, dedup_key="vault.save")
report.report_warn("cache miss", cause=exc, dedup_key="cache.read")
report.report_quiet("degraded, fell back", cause=exc, dedup_key="idx.stale")
report.notify("you appear to be offline", cause=exc, dedup_key="conn.offline")
report.report_panic("ipc-loop", recovered)   # fatal, fingerprint panic:<name>
report.crumb("ipc", "frame decoded")

# Background work under capture (GoSafe analog):
report.run_task("importer", lambda: do_import())               # threads
report.run_task_async("importer", do_import_async())           # asyncio
report.run_task("reindex", do_reindex, quiet=True)             # telemetry only
```

## Lanes

Three sinks: the local log (always), the user lane (`set_notifier`), and Sentry
capture (behind the readiness + rate-limit gate).

| verb            | local log | user lane        | capture |
|-----------------|-----------|------------------|---------|
| `report_error`  | error     | notice `error`   | error   |
| `report_warn`   | warning   | notice `warning` | warning |
| `report_quiet`  | warning   | -                | warning |
| `notify`        | warning   | notice `notice`  | -       |
| `report_panic`  | fatal     | notice `fatal`   | fatal   |

The local log always runs; the user lane is dispatched unconditionally, before
the readiness + rate-limit gate; capture runs last, behind that gate. So a
failure never loses both telemetry and user visibility.

`set_notifier` registers a `Callable[[Notice], None]`; passing `None` clears it.
With no notifier the user lane is a no-op. `Notice` carries `msg`, `level`,
`dedup_key`, `cause`, and `tags`; the app owns rendering and any coalescing
(keyed on `dedup_key`). The callback runs on the caller's thread. A notifier
that raises never breaks the caller's path and never recurses into the user
lane: it is caught and captured telemetry-only.

## Semantics

- **Empty DSN = log-only no-op.** Safe to call before `init`; capture stays
  disabled and every call still logs locally and to the user lane.
- **Log-before-drop.** Every capture writes one structured local line before the
  readiness and rate-limit checks, so nothing is lost in log-only mode.
- **60s in-memory rate limit** keyed by `dedup_key`; the same key is the Sentry
  fingerprint (grouping). An empty key opts out of rate limiting. The rate limit
  applies to capture only - the user lane is never rate-limited, and `notify`
  touches no rate-limit state, so a `notify` never consumes the next capture's
  slot for the same key.
- **Per-name fingerprints.** `panic:<name>` for `report_panic`, `task:<name>`
  for `run_task` / `run_task_async`.
- **Background tasks surface by default; `quiet=True` is telemetry-only.** A
  task failure (raised or returned exception) captures under `task:<name>` and
  feeds the user lane; `quiet=True` routes it at warning with no user lane
  (capture yes, user lane no).
- **Concurrency-isolated capture.** Every capture site forks `new_scope()`; the
  background-task wrappers additionally fork `isolation_scope()` per thread and
  per asyncio task, so concurrent captures cannot bleed scope or fingerprint.

See `DESIGN.md` for the design forks and the sentry-sdk version behavior relied
on.

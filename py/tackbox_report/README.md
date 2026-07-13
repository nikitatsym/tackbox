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

report.report_error("db write failed", cause=exc, dedup_key="vault.save")
report.report_warn("cache miss", cause=exc, dedup_key="cache.read")
report.report_panic("ipc-loop", recovered)   # fatal, fingerprint panic:<name>
report.crumb("ipc", "frame decoded")

# Background work under capture (GoSafe analog):
report.run_task("importer", lambda: do_import())         # threads
report.run_task_async("importer", do_import_async())     # asyncio
```

## Semantics

- **Empty DSN = log-only no-op.** Safe to call before `init`; capture stays
  disabled and every call still logs locally.
- **Log-before-drop.** Every capture writes one structured local line before the
  readiness and rate-limit checks, so nothing is lost in log-only mode.
- **60s in-memory rate limit** keyed by `dedup_key`; the same key is the Sentry
  fingerprint (grouping). An empty key opts out of rate limiting.
- **Per-name fingerprints.** `panic:<name>` for `report_panic`, `task:<name>`
  for `run_task` / `run_task_async`.
- **Concurrency-isolated capture.** Every capture site forks `new_scope()`; the
  background-task wrappers additionally fork `isolation_scope()` per thread and
  per asyncio task, so concurrent captures cannot bleed scope or fingerprint.

See `DESIGN.md` for the design forks and the sentry-sdk version behavior relied
on.

# tackbox-report (Python)

Direct error-reporting helpers for Python applications using Sentry or
GlitchTip. A thin wrapper over `sentry-sdk`. An empty DSN makes every call a
log-only no-op, so a repo can adopt the API before any Sentry/GlitchTip
endpoint exists.

This is one runtime arm of tackbox. The linter recognizes these helpers as one
valid report outcome; propagating the error or carrying an explained local
exception stays valid too. The linter ships separately as the `tackbox`
distribution; installing this helper does not pull the linter, and vice versa.

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
```

`report_error` and `report_warn` feed the local log, the user lane, and Sentry
capture; `report_quiet` skips the user lane; `notify` feeds only the user lane;
`report_panic` feeds all three. `set_notifier` registers a
`Callable[[Notice], None]` and `None` clears it; `Notice` carries `msg`,
`level`, `dedup_key`, `cause`, and `tags`, and the app owns rendering and any
coalescing keyed on `dedup_key`.

## Runtime contract

Lane routing, telemetry dedup, panic grouping, and capture isolation are the
shared cross-language contract, documented in
[docs/report-contracts.md](https://github.com/nikitatsym/tackbox/blob/main/docs/report-contracts.md).
See `DESIGN.md` for the Python implementation choices.

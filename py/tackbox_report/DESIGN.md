# tackbox_report -- design notes

Python implementation choices for the direct error-reporting helpers. The
shared cross-language behavior lives in
[../../docs/report-contracts.md](../../docs/report-contracts.md); this file
records only what is specific to the `sentry-sdk` implementation.

## sentry-sdk 2.x scope forking

`sentry-sdk` 2.x replaced the Hub/scope-stack model with a forking scope API.
The helper relies on it in two places:

- `sentry_sdk.new_scope()` forks the current scope, applies changes, and
  restores on exit. Every capture site (error / warn / quiet / panic / verify)
  runs inside it, so concurrent captures each mutate their own forked scope and
  cannot bleed fingerprint or tags into one another -- the per-capture
  isolation the shared contract requires.
- `Scope.fingerprint` is a settable property (`scope.fingerprint = [key]`);
  there is no `set_fingerprint` method. Level, tags, and context use
  `set_level` / `set_tag` / `set_context`.

The `<3.0` dependency cap is deliberate: this forking scope API is exactly what
changed across the major version, so per-capture isolation is a 2.x contract.

## Default integrations disabled

`init` passes `default_integrations=False`. This is load-bearing: the default
`LoggingIntegration` would turn the helper's own log-before-drop lines (which
log at ERROR) into a second, un-rate-limited Sentry event. Disabling defaults
keeps this helper the sole capture funnel.

## Local sink and levels

The local log uses stdlib `logging` (logger `tackbox.report`, `propagate=False`,
a `_StructFormatter` that appends `err=` / `tags=`). A `logger=` override
redirects it. Panic logs at a custom `FATAL` level (60, above `CRITICAL`),
added via `addLevelName` without renaming `CRITICAL`.

## init passthrough and verify

`init` forwards unknown kwargs to `sentry_sdk.init` (`before_send`, `transport`,
`sample_rate`, ...); the tests use `before_send` to capture events without a
network. `verify` ships the `report.startup` healthcheck and flushes;
`sentry_sdk.flush()` returns `None`, so the helper cannot detect a delivery
timeout and raises only when called before a successful `init`.

## Packaging

A self-contained distribution `tackbox-report` (import package
`tackbox_report`) with its own `pyproject.toml`, published independently of the
`tackbox` linter so a repo can depend on either without the other. Release
procedure: [../../docs/publishing-helpers.md](../../docs/publishing-helpers.md).
Recognition of these verbs by the Python linter is
[../../rules/DECISIONS.md](../../rules/DECISIONS.md) D004/D010.

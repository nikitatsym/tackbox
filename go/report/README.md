# report (Go)

Capture helper for Go: a thin wrapper over `sentry-go` that emits the
`SentryErr` / `Warn` / `Panic` / `Crumb` API the `erclint` rules
expect. An empty DSN makes every call a log-only no-op, so a repo can
adopt the API (and satisfy the linter) before any Glitchtip endpoint
exists.

This is the runtime half of tackbox. The static half - the analyzer
that forces you to call these helpers - is `erclint`; see
`../README.md`.

## Import

```go
import "github.com/nikitatsym/tackbox/go/report"
```

The module is `github.com/nikitatsym/tackbox`; `go get` it like any
dependency. erclint credits a call as a capture only when its callee
resolves to this exact import path (origin, not name).

## Setup

Call `Init` once at startup and `Flush` on the way out.

```go
func main() {
    err := report.Init(report.Options{
        DSN:         report.DSNFromEnv(), // SENTRY_DSN, then GLITCHTIP_DSN
        Release:     version,
        Environment: "prod",
        Verify:      true, // ship one healthcheck, fail fast if unreachable
    })
    if err != nil {
        log.Fatalf("report init: %v", err)
    }
    defer report.Flush()
    // ...
}
```

`Init` returns nil on an empty DSN (logging a WARN unless
`SilentMissing`), so a missing DSN never blocks startup - it only
disables transmission.

### Options

- `DSN` (`string`) - empty makes every call a log-only no-op.
- `Release` / `Environment` (`string`) - tags stamped on every event.
- `FlushTimeout` (`time.Duration`, default `2s`) - wait budget for
  `Flush`.
- `RateWindow` (`time.Duration`, default `60s`) - per-`dedupKey`
  suppression window.
- `Debug` (`bool`) - pipe sentry transport diagnostics to stderr.
- `Verify` (`bool`) - send a startup healthcheck and block on flush.
- `VerifyTimeout` (`time.Duration`, default `3s`) - timeout for
  `Verify`.
- `SilentMissing` (`bool`) - suppress the WARN log on an empty DSN.
- `Logger` (`*slog.Logger`) - override the local sink; nil uses a JSON
  handler on stderr.

## API

```go
func Init(opts Options) error
func DSNFromEnv() string
func Ready() bool
func Verify(timeout time.Duration) error
func Flush(timeout ...time.Duration)

func SentryErr(ctx context.Context, msg string, err error,
    tags map[string]string, dedupKey string)
func Warn(ctx context.Context, msg string, err error,
    tags map[string]string, dedupKey string)
func Panic(name string, recovered any)
func Crumb(category, message string, data map[string]any)

func GoSafe(name string, fn func() error)
func WrapHandler(name string, h http.Handler) http.Handler
```

- `SentryErr` - level error; an unrecoverable failure you handle here.
- `Warn` - level warning; a transient or external fault you recovered
  from.
- `Panic` - level fatal; pass the `recover()` value.
- `Crumb` - a breadcrumb toward the next capture; not itself an event.

erclint credits only `SentryErr`, `Warn`, and `Panic` as captures;
`Init`, `Flush`, `Verify`, `Ready`, and `Crumb` are not. In an
`if err != nil` branch, a capture is one of those three - anything
else leaves the branch uncovered.

```go
// Terminal handling: capture and continue. Do NOT also `return err`
// in the same branch - erclint ERC005 forbids capture + propagate.
if err := writeCache(v); err != nil {
    report.Warn(ctx, "cache write failed, continuing", err, nil, "cache.write")
}
```

## dedupKey

`dedupKey` becomes the event fingerprint (Sentry grouping) and the
rate-limit key: repeat events with the same key inside `RateWindow`
(60s) are dropped. Convention: `area.suffix`, e.g. `vault.save`,
`agent.list`. An empty key is never rate-limited and lets Sentry
auto-group.

Fingerprint arguments must not name secrets (`token`, `password`,
`key`, `secret`, `cookie`) or carry raw user input - erclint ERC006
rejects them.

## Local logging

Every `SentryErr` / `Warn` / `Panic` writes one structured JSON line
via `log/slog` *before* the readiness and rate-limit checks, so
nothing is lost when capture is disabled. The default sink is a JSON
handler on stderr (ISO 8601 timestamp, level, message); pass
`Options.Logger` to redirect it or reuse an existing `*slog.Logger`.

One physical line, shown wrapped:

```json
{
  "time": "2026-07-06T02:14:09.481+02:00",
  "level": "ERROR",
  "msg": "unlock failed",
  "err": "bad passphrase",
  "tags": { "item": "work-key" }
}
```

`tags` ride along as a nested `tags` object (keys sorted), so full
context survives in log-only mode - this is the record you grep when
there is no Glitchtip. `dedupKey` is deliberately *not* logged: it
routes the Sentry event, it is not diagnostics. `Panic` logs at a
custom `FATAL` level with `recovered` and `stack` attributes. `Crumb`
is capture-only: it records a breadcrumb when ready and emits no local
line.

## Coverage primitives

Use these instead of raw goroutines and handlers so failures in
background work are captured, not swallowed:

```go
report.GoSafe("ipc-accept", func() error { return srv.Accept() })
handler = report.WrapHandler("api", handler)
```

- `GoSafe(name, fn)` runs `fn` in a goroutine under `recover`; a panic
  goes through `Panic`, a returned error through `SentryErr`.
- `WrapHandler(name, h)` recovers panics in `h` and turns them into a
  500 plus a capture. With `Init` never called it still installs a
  minimal recover.

## See also

- `../README.md` - the `erclint` ruleset that enforces these calls.
- `../../README.md` - tackbox overview and wiring into `dev.py lint`.

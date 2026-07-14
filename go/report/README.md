# report (Go)

Capture helper for Go: a thin wrapper over `sentry-go` that emits the
`Error` / `Warn` / `Quiet` / `Notify` / `Panic` / `Crumb` API the
`erclint` rules expect. An empty DSN makes every call a log-only no-op,
so a repo can adopt the API (and satisfy the linter) before any Glitchtip
endpoint exists.

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
  suppression window (telemetry only; see "Lanes").
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

func Error(ctx context.Context, msg string, err error,
    tags map[string]string, dedupKey string)
func Warn(ctx context.Context, msg string, err error,
    tags map[string]string, dedupKey string)
func Quiet(ctx context.Context, msg string, err error,
    tags map[string]string, dedupKey string)
func Notify(ctx context.Context, msg string, err error,
    tags map[string]string, dedupKey string)
func Panic(name string, recovered any, opts ...Option)
func Crumb(category, message string, data map[string]any)

func SetNotifier(fn func(Notice))
type Notice struct {
    Msg      string
    Level    string
    Tags     map[string]string
    DedupKey string
    Cause    error
}

func GoSafe(name string, fn func() error, opts ...Option)
func WrapHandler(name string, h http.Handler) http.Handler
func Silent() Option
```

- `Error` - level error; an unrecoverable failure you handle here.
- `Warn` - level warning; a transient or external fault you recovered
  from.
- `Quiet` - level warning, telemetry only (no user lane); a background,
  self-healed, or degraded-with-fallback failure.
- `Notify` - user lane only, no capture; an expected environmental fault
  (the user lost connectivity). `err` is the caught error the notice is
  about.
- `Panic` - level fatal; pass the `recover()` value.
- `Crumb` - a breadcrumb toward the next capture; not itself an event.

erclint credits `Error`, `Warn`, and `Panic` as captures; `Init`,
`Flush`, `Verify`, `Ready`, `Notify`, and `Crumb` are not. In an
`if err != nil` branch, a capture is one of those three - anything else
leaves the branch uncovered.

```go
// Terminal handling: capture and continue. Do NOT also `return err`
// in the same branch - erclint ERC005 forbids capture + propagate.
if err := writeCache(v); err != nil {
    report.Warn(ctx, "cache write failed, continuing", err, nil, "cache.write")
}
```

## Lanes

Every verb writes to a subset of three lanes:

| verb     | local log | user lane        | capture (gated) |
|----------|-----------|------------------|-----------------|
| `Error`  | error     | notice `error`   | error           |
| `Warn`   | warn      | notice `warning` | warning         |
| `Quiet`  | warn      | -                | warning         |
| `Notify` | warn      | notice `notice`  | -               |
| `Panic`  | fatal     | notice `fatal`   | fatal           |

Ordering per call: the local log always runs; the user lane is
dispatched unconditionally, before the init and rate-limit gate; capture
runs last, behind that gate. So a failure never loses both telemetry and
user visibility, and the user lane is never suppressed by the helper
(tackbox `rules/DECISIONS.md` D005).

## User lane

`SetNotifier` registers a `func(Notice)` sink; passing `nil` clears it.
With no notifier the user lane is a no-op (the local log and capture
still run). The callback runs on the caller's goroutine - the app bridges
to its UI thread itself - and registration is concurrency-safe. The app
owns rendering and any coalescing (a storm of identical `Notice`s keyed
on the same `DedupKey` becomes one banner, a counter, or a per-click
toast - presentation policy the helper does not impose). A notifier that
panics never breaks the caller's path and never recurses into the user
lane: it is caught and captured telemetry-only.

## dedupKey

`dedupKey` becomes the event fingerprint (Sentry grouping) and the
rate-limit key: repeat captures with the same key inside `RateWindow`
(60s) are dropped. The rate limit applies to capture only - the user
lane is never rate-limited, and `Notify` touches no rate-limit state, so
a `Notify` never consumes the next capture's slot for the same key.
Convention: `area.suffix`, e.g. `vault.save`, `agent.list`. An empty key
is never rate-limited and lets Sentry auto-group.

Capture arguments (message, tags, dedupKey) must not carry raw user
input, and the dedupKey must be a well-formed literal - erclint ERC006
rejects violations.

## Local logging

Every `Error` / `Warn` / `Quiet` / `Notify` / `Panic` writes one
structured JSON line via `log/slog` *before* the readiness and rate-limit
checks, so nothing is lost when capture is disabled. The default sink is
a JSON handler on stderr (ISO 8601 timestamp, level, message); pass
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
report.GoSafe("indexer", buildIndex, report.Silent()) // telemetry only
handler = report.WrapHandler("api", handler)
```

- `GoSafe(name, fn, opts...)` runs `fn` in a goroutine under `recover`; a
  panic goes through `Panic`, a returned error through the error lane,
  each under a per-name fingerprint (`panic:<name>`, `go.task:<name>`).
  A failure surfaces to the user lane by default; `Silent()` routes it
  telemetry-only (capture yes, user lane no). `Panic` accepts the same
  `Silent()` opt.
- `WrapHandler(name, h)` recovers panics in `h` and turns them into a
  500 plus a capture. With `Init` never called it still installs a
  minimal recover.

## See also

- `../README.md` - the `erclint` ruleset that enforces these calls.
- `../../README.md` - tackbox overview and wiring into `dev.py lint`.

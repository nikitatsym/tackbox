# report (Java) - first-cut design

Runtime capture helper for Java: a thin wrapper over sentry-java with the
`error` / `warn` / `panic` / `crumb` API the tackbox error-reporting spec
expects. Empty DSN = log-only no-op. Java mirror of `go/report/report.go`
and `js/report.js`.

FIRST CUT, for review. Nothing ships: not wired into `javalint.jar`, the
thin wheel, or any publish step. Standalone module, built on its own.

## Status

- Files: `pom.xml`, `src/main/java/nl/tsym/tackbox/report/{Report,Options}.java`,
  `src/test/java/nl/tsym/tackbox/report/ReportTest.java`.
- Build/test: `mvn -f java/report/pom.xml test` -> 4 tests green.
- sentry-java: `io.sentry:sentry:7.22.6`.
- Isolation API: `Sentry.getCurrentHub().clone()` then `hub.withScope(...)` /
  `hub.captureException(...)` per capture - the 1:1 analog of go/report's
  `sentry.CurrentHub().Clone()` (DECISIONS D003).

## API (mirrors go/report)

    void    init(Options opts)            // empty DSN -> log-only no-op
    boolean ready()
    String  dsnFromEnv()                  // SENTRY_DSN, then GLITCHTIP_DSN
    void    verify(long timeoutMillis)    // healthcheck (report.startup)
    void    flush() / flush(long timeoutMillis)

    void error(String msg, Throwable cause,
               Map<String,String> tags, String dedupKey)   // ERROR
    void warn(String msg, Throwable cause,
              Map<String,String> tags, String dedupKey)    // WARNING
    void panic(String name, Object recovered)              // FATAL
    void crumb(String category, String message, Map<String,Object> data)

    Runnable safeRunnable(String name, Runnable body)      // GoSafe analog
    <T> Callable<Optional<T>> safeCallable(String name, Callable<T> body)

- `error` / `warn` / `panic` log locally (System.Logger) BEFORE the readiness
  and rate-limit checks (log-before-drop invariant), so a dropped or
  capture-disabled event still leaves a local record.
- `dedupKey` is both the Sentry fingerprint and the in-memory rate-limit key;
  a repeat with the same key inside the window (default 60s) is dropped.
  `ConcurrentHashMap<String,Long>` keyed on dedupKey. The first-hit check is a
  load-then-put with the same benign race go/report's `sync.Map` has.
- Per-name fingerprints (DECISIONS D002): `panic:<name>`, and the task wrapper
  `task:<name>` (mirror of go's `go.task:<name>`, minus the go-only prefix).

## Load-bearing forks (defaults + alternatives)

### 1. Module layout / packaging

- CHOSEN: standalone Maven module at `java/report/` with its own `pom.xml`,
  NOT part of any reactor. There is no aggregator/root pom in the repo
  (`java/pom.xml` is the javalint jar module itself), so this module is
  invisible to the javalint build by construction - the cleanest way to keep
  javalint's shaded-jar build undisturbed.
- Package: `nl.tsym.tackbox.report` (sibling to `nl.tsym.tackbox.javalint`).
- Intended coordinates if ever published: groupId `nl.tsym.tackbox`,
  artifactId `report` (or `tackbox-report`), version tracked with tackbox.
  NOT published here.
- Alternative considered: a package added to the existing javalint module.
  Rejected - it would drag sentry-java into the javalint classpath and the
  shaded jar, coupling the linter to a runtime SDK it must not carry.

### 2. GoSafe analog (Runnable + Callable)

- `safeRunnable(name, body)` IMPLEMENTED: the faithful GoSafe analog for
  `executor.submit(...)` / raw threads. A thrown Exception is captured under
  `task:<name>` then swallowed (fire-and-forget, like GoSafe). Catches
  `Exception`, not `Throwable`: an unrecoverable Error propagates uncaught
  (javalint JV003).
- `safeCallable(name, body)` IMPLEMENTED as `Callable<Optional<T>>`:
  report-and-swallow, `Optional.empty()` on failure. It does NOT rethrow.
  Report + rethrow is a double-capture the spec forbids (capture-or-propagate;
  javalint JV006) because an upstream `future.get()` handler would report the
  same failure again. `Optional.empty()` is the value-world equivalent of
  GoSafe swallowing. A caller that must observe the exception should not wrap -
  it should catch and call `error()` at its own single capture site.
- DEFERRED: an `UncaughtExceptionHandler` installer / a ScheduledExecutor
  decorator that routes swallowed periodic-task failures through `panic` /
  `task:<name>`. Not needed for the first cut.

### 3. Local-log sink: System.Logger vs slf4j

- CHOSEN: `java.lang.System.Logger` (JEP 264). Zero extra dependency (JDK
  built-in), matches the real consumer reference (`InternalLog.java` uses
  `System.Logger`), and javalint tier-1 recognizes it as a capture sink
  (`log(Level.ERROR|WARNING, ..., caught)`). Overridable via `Options.logger`.
- Alternative: slf4j (`org.slf4j.Logger.error/warn`), also tier-1 recognized.
  Deferred - it adds an api dependency plus a binding choice (logback/etc.) the
  consumer must supply, heavier than a first cut needs. Trivial to switch: the
  local sink is one field and one helper.
- Note: `System.Logger` has no FATAL level, so `panic` logs locally at ERROR;
  the shipped Sentry event still carries level FATAL.

### 4. WrapHandler / servlet-filter analog

- DEFERRED for the first cut (noted, as required). go/report's `WrapHandler`
  wraps an `http.Handler`; the Java analog is a `jakarta.servlet.Filter` (or a
  Spring `HandlerInterceptor` / `OncePerRequestFilter`) that recovers, captures
  under `panic:http.<name>`, and returns 500. It needs a servlet API dependency
  and a framework choice, so it is out of scope here. `panic(name, recovered)`
  already provides the capture primitive such a filter would call.

## Known divergences from go/report

- `Sentry.flush(long)` returns void in sentry-java, so `verify` ships the
  healthcheck and flushes but cannot report delivery failure the way go/js do
  (they get a boolean from flush). `verify` throws only when not initialized.
- `capture` preserves the original throwable's type/stack (like js/report) and
  carries `msg` in a `tackbox` context, rather than wrapping msg+cause into a
  new error (go). Fingerprint grouping is unaffected (it is set explicitly).
- Concurrency isolation uses the 7.x `IHub.clone()` idiom. sentry-java 8.x
  replaced `IHub` with the Scopes API (`forkedScopes` / `withIsolationScope`);
  a bump to 8.x would swap `clone()` for the forked-scope call - same D003
  guarantee, different spelling. 7.x was chosen because its clone idiom is the
  exact mirror of the Go reference.

## Options

`dsn`, `release`, `environment`, `flushTimeoutMillis` (2000),
`rateWindowMillis` (60000), `debug`, `verify`, `verifyTimeoutMillis` (3000),
`silentMissing`, `logger` (System.Logger override), `beforeSend`
(SentryOptions.BeforeSendCallback - scrub/drop hook; the tests use it to
intercept events without shipping).

## Tests (ReportTest)

- `initNoOpOnEmptyDsn`: empty DSN -> `ready()==false`, `error(...)` ships nothing.
- `rateLimitDropsRepeatInWindow`: two `error` calls, same dedupKey -> one event.
- `taskWrapperUsesPerNameFingerprint`: `safeRunnable` throwing body -> one event
  with fingerprint `task:<name>`, level ERROR, tag `task=<name>`.
- `concurrentCapturesDoNotBleedScope`: 32 captures across an 8-thread pool
  released together -> 32 events, each fingerprint exactly its own key, all keys
  present once (no scope bleed - validates the hub-clone isolation).

Interception uses `Options.beforeSend` returning null (records the event,
never ships). The SEVERE stack traces on stderr during the run are the
log-before-drop local records, not failures.

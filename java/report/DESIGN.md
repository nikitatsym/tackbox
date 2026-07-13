# report (Java) - design

Runtime capture helper for Java: a thin wrapper over sentry-java with the
`error` / `warn` / `panic` / `crumb` API the tackbox error-reporting spec
expects. Empty DSN = log-only no-op. Java mirror of `go/report/report.go`
and `js/report.js`.

Standalone module, built on its own. It emits an OSGi bundle and is
Maven-Central-ready, but nothing is published here: no GPG signing, no
Central deploy, no reactor/CI wiring (see "Packaging").

## Status

- Files: `pom.xml`,
  `src/main/java/nl/tsym/tackbox/report/{Report,Options}.java`,
  `src/test/java/nl/tsym/tackbox/report/ReportTest.java`.
- Build/test: `mvn -f java/report/pom.xml test` -> 8 tests green.
  `mvn -f java/report/pom.xml package` emits the OSGi bundle jar plus the
  sources and javadoc jars.
- sentry-java: `io.sentry:sentry:8.47.0` (8.x, current major).
- Coordinates: `nl.tsym.tackbox:report:0.1.0`, packaging `bundle`.
- Isolation API: `Sentry.captureException(t, scope -> ...)` /
  `Sentry.captureMessage(msg, scope -> ...)` per capture - the 8.x
  Scopes-API analog of go/report's `sentry.CurrentHub().Clone()`
  (DECISIONS D003). See "Concurrency isolation".

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

    void installUncaughtHandler()    // route uncaught -> panic(threadName)
    void uninstallUncaughtHandler()  // restore prior default handler
    ExecutorService wrap(String name, ExecutorService delegate)

- `error` / `warn` / `panic` log locally (System.Logger) BEFORE the
  readiness and rate-limit checks (log-before-drop invariant), so a
  dropped or capture-disabled event still leaves a local record.
- `dedupKey` is both the Sentry fingerprint and the in-memory rate-limit
  key; a repeat with the same key inside the window (default 60s) is
  dropped. `ConcurrentHashMap<String,Long>` keyed on dedupKey. The
  first-hit check is a load-then-put with the same benign race go/report's
  `sync.Map` has.
- Per-name fingerprints (DECISIONS D002): `panic:<name>`, and the task
  wrapper `task:<name>` (mirror of go's `go.task:<name>`, minus the
  go-only prefix).

## Concurrency isolation (D003) on sentry-java 8.x

sentry-java 8.x removed the `Hub` / `IHub` model - and with it
`IHub.clone()` + `hub.withScope` - for the Scopes API. Each capture site
now ships through the scope-callback overload:

    Sentry.captureException(t, scope -> {        // panic, error/warn core
        scope.setLevel(level);
        scope.setFingerprint(List.of(dedupKey));
        // tags, contexts ...
    });
    Sentry.captureMessage("report.verify", scope -> { /* ... */ }); // verify

The overload builds a per-event local scope by cloning the current
combined scope, runs the callback against that clone, and applies it to
that one event only - it never mutates shared or current scope. So two
background threads capturing at the same instant cannot swap
fingerprint/tags: the D003 guarantee that lets D002's per-name
fingerprints hold under real concurrency. This is the direct analog of
go/report cloning the hub per capture; `captureException(t, callback)` is
the 8.x spelling of `hub.Clone()` then `hub.WithScope(...)`.

`concurrentCapturesDoNotBleedScope` (32 captures over an 8-thread pool,
released together) is the regression guard: each event carries exactly its
own fingerprint, all keys present once, no bleed.

## Installers (executor / thread world)

The deferred goSafe surface, now built:

- `installUncaughtHandler()` sets a `Thread` default uncaught handler that
  routes any thread's uncaught throwable through
  `panic(thread.getName(), throwable)` (fingerprint `panic:<threadName>`),
  then chains the handler present at install time. Idempotent (a second
  install while installed is a no-op, never a double-wrap) and restorable
  (`uninstallUncaughtHandler()` puts the prior handler back).
- `wrap(name, ExecutorService)` returns an `ExecutorService` whose
  `execute` / `submit` run every task report-and-swallow under
  `task:<name>` (like `safeRunnable`). `invokeAll` / `invokeAny` delegate
  unwrapped: they hand results and exceptions straight back to the caller,
  who captures at their own single site, so wrapping them would
  double-capture (JV006). The `submit(Callable)` path must keep the
  `Future<T>` contract, so a captured failure yields `null` (not the
  `Optional.empty()` that public `safeCallable` returns). Double-wrapping a
  `safeRunnable` is safe: the inner catch fires first, so the outer never
  re-captures.

`init` disables sentry's own default uncaught handler
(`setEnableUncaughtExceptionHandler(false)`). The helper owns the uncaught
story via `installUncaughtHandler()` - opt-in, per-name fingerprint,
log-before-drop, rate-limited - mirroring go/report, which installs no
global handler on init. Leaving sentry's on would also double-capture: its
integration would ship the same uncaught throwable a second time under
default grouping when our handler chains to it.

## Packaging

### OSGi bundle

`mvn package` builds an OSGi bundle via `maven-bundle-plugin` (packaging
`bundle`). The emitted `MANIFEST.MF` carries:

    Bundle-SymbolicName: nl.tsym.tackbox.report
    Export-Package:      nl.tsym.tackbox.report;version="0.1.0"
    Import-Package:      io.sentry;version="[8,9)",
                         io.sentry.protocol;version="[8,9)", ...

for the Eclipse/Equinox (sts-wand) stack. `java.lang.System.Logger` needs
no `Import-Package`: `java.*` is boot-delegated in OSGi, never imported.
`io.sentry` is imported (not embedded) because `Options.beforeSend` leaks
`io.sentry.SentryOptions.BeforeSendCallback`, so bnd declares the export
`uses:="io.sentry"`.

### OSGi + the sentry-java dependency

sentry-java 8.x ships plain jars with NO OSGi manifest (no
`Bundle-SymbolicName` / `Export-Package`); verified against
`io.sentry:sentry:8.47.0`, whose jar carries only vendor metadata. So on
Equinox our bundle's `Import-Package: io.sentry` does not resolve until a
sentry bundle is present.

CHOSEN default: import `io.sentry` and require the consumer to supply
sentry-java as a bundle. sentry core has zero runtime dependencies, so
wrapping it is a single-jar step (bnd `wrap`, the p2 "Wrap" action, or
`maven-bundle-plugin` run over the sentry jar). This keeps our bundle lean
and its manifest honest - we import exactly what we use - and avoids our
small helper becoming the platform's sentry provider.

ALTERNATIVE (documented, not enabled): embed sentry so the bundle is
self-contained (drop-in, no consumer-side wrapping) -

    <Embed-Dependency>sentry</Embed-Dependency>
    <_exportcontents>io.sentry.*</_exportcontents>

Tradeoff: the bundle then carries a private copy of sentry and re-exports
`io.sentry`; if the platform later gains a real sentry bundle there are two
copies of the `io.sentry` class space, a wiring hazard. Given sts-wand is a
controlled Equinox target either is workable; the lean import is the
default because its manifest is verifiable and correct without first
solving sentry's own (upstream-unsolved) OSGi metadata. Flip by adding the
two instructions above and dropping the `Import-Package: io.sentry` range.

### Maven Central

The pom is Central-ready: `nl.tsym.tackbox:report:0.1.0` with `name`,
`description`, `url`, MIT `licenses`, `developers`, `scm`, and attached
sources + javadoc jars (`maven-source-plugin`, `maven-javadoc-plugin`).

NOT done here - a publish still needs: GPG-sign all artifacts
(`maven-gpg-plugin`), Central Portal credentials plus the publish plugin
(`central-publishing-maven-plugin`, or the OSSRH staging flow), and a real
release version. No CI or publish wiring is added; nothing is signed or
deployed.

## Load-bearing forks (defaults + alternatives)

### 1. Module layout / packaging

- CHOSEN: standalone Maven module at `java/report/` with its own `pom.xml`,
  NOT part of any reactor. There is no aggregator/root pom in the repo
  (`java/pom.xml` is the javalint jar module itself), so this module is
  invisible to the javalint build by construction - the cleanest way to
  keep javalint's shaded-jar build undisturbed.
- Package: `nl.tsym.tackbox.report` (sibling to `nl.tsym.tackbox.javalint`).
- Coordinates: groupId `nl.tsym.tackbox`, artifactId `report`, version
  `0.1.0` (the first release coordinate, distinct from the linter's 0.0.0
  dev version). Central-ready but not published (see "Packaging").
- Alternative considered: a package added to the existing javalint module.
  Rejected - it would drag sentry-java into the javalint classpath and the
  shaded jar, coupling the linter to a runtime SDK it must not carry.

### 2. GoSafe analog + installers

- `safeRunnable(name, body)` IMPLEMENTED: the faithful GoSafe analog for
  `executor.submit(...)` / raw threads. A thrown Exception is captured
  under `task:<name>` then swallowed (fire-and-forget, like GoSafe).
  Catches `Exception`, not `Throwable`: an unrecoverable Error propagates
  uncaught (javalint JV003).
- `safeCallable(name, body)` IMPLEMENTED as `Callable<Optional<T>>`:
  report-and-swallow, `Optional.empty()` on failure. It does NOT rethrow.
  Report + rethrow is a double-capture the spec forbids (javalint JV006)
  because an upstream `future.get()` handler would report the same failure
  again. A caller that must observe the exception should not wrap - it
  should catch and call `error()` at its own single capture site.
- `installUncaughtHandler()` / `uninstallUncaughtHandler()` and
  `wrap(name, ExecutorService)` IMPLEMENTED - see "Installers". This is the
  deferred surface the first cut listed; it is now built.

### 3. Local-log sink: System.Logger vs slf4j

- CHOSEN: `java.lang.System.Logger` (JEP 264). Zero extra dependency (JDK
  built-in), matches the real consumer reference (`InternalLog.java` uses
  `System.Logger`), and javalint tier-1 recognizes it as a capture sink
  (`log(Level.ERROR|WARNING, ..., caught)`). Overridable via
  `Options.logger`.
- Alternative: slf4j (`org.slf4j.Logger.error/warn`), also tier-1
  recognized. Deferred - it adds an api dependency plus a binding choice
  (logback/etc.) the consumer must supply, heavier than this needs.
- Note: `System.Logger` has no FATAL level, so `panic` logs locally at
  ERROR; the shipped Sentry event still carries level FATAL.

### 4. WrapHandler / servlet-filter analog

- DEFERRED (noted, as required). go/report's `WrapHandler` wraps an
  `http.Handler`; the Java analog is a `jakarta.servlet.Filter` (or a
  Spring `OncePerRequestFilter`) that recovers, captures under
  `panic:http.<name>`, and returns 500. It needs a servlet API dependency
  and a framework choice, so it is out of scope here.
  `panic(name, recovered)` already provides the capture primitive such a
  filter would call.

## Known divergences from go/report

- `Sentry.flush(long)` returns void in sentry-java, so `verify` ships the
  healthcheck and flushes but cannot report delivery failure the way go/js
  do (they get a boolean from flush). `verify` throws only when not
  initialized.
- `capture` preserves the original throwable's type/stack (like js/report)
  and carries `msg` in a `tackbox` context, rather than wrapping msg+cause
  into a new error (go). Fingerprint grouping is unaffected (it is set
  explicitly).
- `installUncaughtHandler()` routes through `panic()` and does not flush;
  an uncaught exception that kills the JVM may exit before delivery. For
  guaranteed delivery on process death, register `flush()` in a shutdown
  hook. (sentry's built-in handler flushes; it is disabled here - see
  "Installers".)

## Options

`dsn`, `release`, `environment`, `flushTimeoutMillis` (2000),
`rateWindowMillis` (60000), `debug`, `verify`, `verifyTimeoutMillis`
(3000), `silentMissing`, `logger` (System.Logger override), `beforeSend`
(SentryOptions.BeforeSendCallback - scrub/drop hook; the tests use it to
intercept events without shipping).

## Tests (ReportTest)

- `initNoOpOnEmptyDsn`: empty DSN -> `ready()==false`, `error(...)` ships
  nothing.
- `rateLimitDropsRepeatInWindow`: two `error` calls, same dedupKey -> one
  event.
- `taskWrapperUsesPerNameFingerprint`: `safeRunnable` throwing body -> one
  event with fingerprint `task:<name>`, level ERROR, tag `task=<name>`.
- `concurrentCapturesDoNotBleedScope`: 32 captures across an 8-thread pool
  released together -> 32 events, each fingerprint exactly its own key, all
  keys present once (validates the per-capture local-scope isolation).
- `wrappedExecutorCapturesThrowingTask`: `wrap(...).execute(throwing)` ->
  one `task:<name>` event.
- `wrappedExecutorCallableSwallowsToNull`: `wrap(...).submit(throwing
  Callable)` -> `future.get()` is `null`, one `task:<name>` event.
- `uncaughtHandlerCapturesUnderPanicFingerprint`: an installed handler on a
  thread that throws -> one FATAL `panic:<threadName>` event.
- `uncaughtHandlerInstallIsIdempotentAndRestorable`: second install is a
  no-op; uninstall restores the pre-install handler.

Interception uses `Options.beforeSend` returning null (records the event,
never ships). The SEVERE stack traces on stderr during the run are the
log-before-drop local records, not failures.

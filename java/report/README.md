# report (Java)

Direct error-reporting helpers for Java applications using Sentry or GlitchTip.
A thin wrapper over `sentry-java`. An empty DSN makes every call a log-only
no-op, so a repo can adopt the API before any Sentry/GlitchTip endpoint exists.

This is one runtime arm of tackbox. The linter recognizes these helpers as one
valid report outcome; propagating the error or carrying an explained local
exception stays valid too. The linter ships separately; installing this helper
does not pull it, and vice versa.

## Install

Maven Central coordinates `io.github.nikitatsym:report`; the Java package is
`nl.tsym.tackbox.report`.

```xml
<dependency>
  <groupId>io.github.nikitatsym</groupId>
  <artifactId>report</artifactId>
  <version>0.2.0</version>
</dependency>
```

Requires Java 17+ and `sentry-java` 8.x.

## Use

```java
import nl.tsym.tackbox.report.Options;
import nl.tsym.tackbox.report.Report;

Report.init(new Options().dsn(Report.dsnFromEnv()));  // empty DSN -> log-only
Report.setNotifier(notice -> render(notice));         // user-lane sink

Report.error("db write failed", cause, Map.of(), "vault.save");
Report.warn("cache miss", cause, Map.of(), "cache.read");
Report.quiet("degraded, fell back", cause, Map.of(), "idx.stale");
Report.notify("you appear to be offline", cause, Map.of(), "conn.offline");
Report.panic("ipc-loop", recovered);                  // fatal, fingerprint panic:<name>
Report.crumb("ipc", "frame decoded", Map.of());
```

`error` and `warn` feed the local log, the user lane, and Sentry capture;
`quiet` skips the user lane; `notify` feeds only the user lane; `panic` feeds
all three. `setNotifier` registers a `Consumer<Notice>` and `null` clears it;
`Notice` carries `msg`, `level`, `tags`, `dedupKey`, and `cause`, and the app
owns rendering and any coalescing keyed on `dedupKey`.

## Runtime contract

Lane routing, telemetry dedup, panic grouping, and capture isolation are the
shared cross-language contract, documented in
[../../docs/report-contracts.md](../../docs/report-contracts.md). See
`DESIGN.md` for the Java implementation choices.

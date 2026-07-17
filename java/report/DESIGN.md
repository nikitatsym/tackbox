# report (Java) - design

Java implementation choices for the direct error-reporting helpers. The shared
cross-language behavior lives in
[../../docs/report-contracts.md](../../docs/report-contracts.md); this file
records only what is specific to the `sentry-java` implementation. Release
procedure: [../../docs/publishing-helpers.md](../../docs/publishing-helpers.md).

Standalone Maven module, built on its own (`mvn -f java/report/pom.xml test`),
outside any reactor so the javalint build never sees sentry-java. It emits an
OSGi bundle and publishes to Maven Central as `io.github.nikitatsym:report`.

## Concurrency isolation on sentry-java 8.x

Each capture site ships through the scope-callback overload:

    Sentry.captureException(t, scope -> {        // panic, error/warn core
        scope.setLevel(level);
        scope.setFingerprint(List.of(dedupKey));
    });
    Sentry.captureMessage("report.verify", scope -> { /* ... */ }); // verify

The overload builds a per-event local scope forked from the current one, runs
the callback against that fork, and applies it to that one event - it never
mutates shared scope. So two threads capturing at once cannot swap
fingerprint/tags: the per-capture isolation the shared contract (D003)
requires.

## Local sink: System.Logger

The local log uses `java.lang.System.Logger` (JEP 264): zero extra dependency,
and javalint tier-1 recognizes it as a capture sink at ERROR / WARNING.
Overridable via `Options.logger`. `System.Logger` has no FATAL level, so
`panic` logs locally at ERROR while the shipped event carries level FATAL.

## OSGi packaging

`mvn package` builds an OSGi bundle via `maven-bundle-plugin` (packaging
`bundle`). The emitted `MANIFEST.MF` exports `nl.tsym.tackbox.report` and
imports `io.sentry` in the `[8,9)` range. `java.lang.System.Logger` needs no
`Import-Package`: `java.*` is boot-delegated in OSGi, never imported.
`io.sentry` is imported because `Options.beforeSend` leaks
`io.sentry.SentryOptions.BeforeSendCallback`, so bnd declares the export
`uses:="io.sentry"`.

sentry-java 8.x ships plain jars with no OSGi manifest, so on Equinox the
consumer supplies sentry-java as a bundle (sentry core has zero runtime
dependencies, so wrapping it is a single-jar step). Importing rather than
embedding keeps the manifest honest - the bundle imports exactly what it uses -
and avoids our small helper becoming the platform's sentry provider.

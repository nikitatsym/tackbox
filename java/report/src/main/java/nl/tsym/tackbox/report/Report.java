package nl.tsym.tackbox.report;

import io.sentry.Breadcrumb;
import io.sentry.Sentry;
import io.sentry.SentryLevel;
import java.lang.System.Logger;
import java.lang.System.Logger.Level;
import java.net.URI;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Runtime capture helper: a thin wrapper over sentry-java with the API the
 * error-reporting spec expects. Empty DSN = log-only no-op, so a repo can adopt
 * the API before any Glitchtip endpoint exists. Java mirror of go/report and
 * js/report.js.
 *
 * <p>Concurrency isolation (DECISIONS D003): every capture ships through
 * sentry-java 8.x's scope-callback overload ({@code Sentry.captureException(t,
 * scope -> ...)} / {@code Sentry.captureMessage(msg, scope -> ...)}), which
 * applies the fingerprint/tags to a per-event local scope forked from the
 * current one - never mutating shared scope - so concurrent captures from
 * background threads cannot bleed into each other. This is the 8.x Scopes-API
 * analog of go/report's {@code sentry.CurrentHub().Clone()}; the 7.x
 * {@code IHub.clone()} + {@code hub.withScope} idiom it replaces was removed
 * when 8.x retired the Hub model.
 */
public final class Report {

    private static volatile boolean ready = false;
    private static volatile long rateWindow = 60_000;
    private static volatile long flushTimeout = 2_000;
    private static final ConcurrentHashMap<String, Long> lastSent = new ConcurrentHashMap<>();
    private static volatile Logger log = System.getLogger("nl.tsym.tackbox.report");

    private Report() {}

    /** Thrown when init cannot bring capture up (e.g. verify before init). */
    public static final class ReportInitException extends RuntimeException {
        public ReportInitException(String message) {
            super(message);
        }
    }

    public static void init(Options opts) {
        if (opts.logger != null) {
            log = opts.logger;
        }
        if (opts.dsn == null || opts.dsn.isEmpty()) {
            ready = false;
            if (!opts.silentMissing) {
                log.log(Level.WARNING,
                        "report: DSN unset, capture disabled (set SENTRY_DSN or GLITCHTIP_DSN)");
            }
            return;
        }
        Sentry.init(sentry -> {
            sentry.setDsn(opts.dsn);
            sentry.setRelease(opts.release);
            sentry.setEnvironment(opts.environment);
            sentry.setDebug(opts.debug);
            // sentry-java 8.x installs its own default uncaught handler on init
            // (fatal, default grouping, no rate limit). This helper owns the
            // uncaught story via installUncaughtHandler() - opt-in, per-name
            // panic:<name> fingerprint, log-before-drop, rate-limited - and
            // mirrors go/report, which installs no global handler on init.
            // Leaving sentry's on would also double-capture when ours wraps it.
            sentry.setEnableUncaughtExceptionHandler(false);
            if (opts.beforeSend != null) {
                sentry.setBeforeSend(opts.beforeSend);
            }
        });
        ready = true;
        if (opts.rateWindowMillis > 0) {
            rateWindow = opts.rateWindowMillis;
        }
        if (opts.flushTimeoutMillis > 0) {
            flushTimeout = opts.flushTimeoutMillis;
        }
        if (opts.verify) {
            verify(opts.verifyTimeoutMillis > 0 ? opts.verifyTimeoutMillis : 3_000);
            log.log(Level.INFO, "report: capture verified, DSN=" + maskDsn(opts.dsn));
            return;
        }
        log.log(Level.INFO, "report: capture enabled, unverified, DSN=" + maskDsn(opts.dsn));
    }

    public static boolean ready() {
        return ready;
    }

    /** SENTRY_DSN, then GLITCHTIP_DSN, then empty. */
    public static String dsnFromEnv() {
        String v = System.getenv("SENTRY_DSN");
        if (v != null && !v.isEmpty()) {
            return v;
        }
        String g = System.getenv("GLITCHTIP_DSN");
        return g != null ? g : "";
    }

    /** Ship one healthcheck (fingerprint report.startup) and flush. Note:
     *  sentry-java's flush returns void, so unlike go/report this cannot report
     *  delivery failure - it sends and drains, throwing only when not ready. */
    public static void verify(long timeoutMillis) {
        if (!ready) {
            throw new ReportInitException("report.verify: not initialized");
        }
        Sentry.captureMessage("report.verify", scope -> {
            scope.setLevel(SentryLevel.INFO);
            scope.setFingerprint(List.of("report.startup"));
            scope.setTag("healthcheck", "true");
        });
        Sentry.flush(timeoutMillis);
    }

    public static void flush() {
        flush(flushTimeout);
    }

    public static void flush(long timeoutMillis) {
        if (!ready) {
            return;
        }
        Sentry.flush(timeoutMillis);
    }

    /** Level error: an unrecoverable failure you handle here. Local log runs
     *  before the rate-limit drop, so a dropped event still leaves a record. */
    public static void error(String msg, Throwable cause, Map<String, String> tags, String dedupKey) {
        logAt(Level.ERROR, msg, cause, tags);
        if (!ready || shouldDrop(dedupKey)) {
            return;
        }
        capture(msg, cause, tags, dedupKey, SentryLevel.ERROR);
    }

    /** Level warning: a transient or external fault you recovered from. */
    public static void warn(String msg, Throwable cause, Map<String, String> tags, String dedupKey) {
        logAt(Level.WARNING, msg, cause, tags);
        if (!ready || shouldDrop(dedupKey)) {
            return;
        }
        capture(msg, cause, tags, dedupKey, SentryLevel.WARNING);
    }

    /** Level fatal, per-name fingerprint panic:&lt;name&gt; (DECISIONS D002).
     *  Pass the caught Throwable (or any recovered value) from an uncaught /
     *  last-resort handler. System.Logger has no FATAL, so the local line is
     *  ERROR; the shipped event carries level FATAL. */
    public static void panic(String name, Object recovered) {
        Throwable t = (recovered instanceof Throwable th)
                ? th
                : new RuntimeException("panic in " + name + ": " + recovered);
        log.log(Level.ERROR, "panic in " + name, t);
        if (!ready) {
            return;
        }
        String key = "panic:" + name;
        if (shouldDrop(key)) {
            return;
        }
        Sentry.captureException(t, scope -> {
            scope.setLevel(SentryLevel.FATAL);
            scope.setTag("source", name);
            scope.setFingerprint(List.of(key));
        });
    }

    /** A breadcrumb toward the next capture; not itself an event. Capture-only,
     *  no local line. Breadcrumbs use the shared hub (D003 known limitation). */
    public static void crumb(String category, String message, Map<String, Object> data) {
        if (!ready) {
            return;
        }
        Breadcrumb b = new Breadcrumb();
        b.setCategory(category);
        b.setMessage(message);
        b.setLevel(SentryLevel.INFO);
        if (data != null) {
            data.forEach(b::setData);
        }
        Sentry.addBreadcrumb(b);
    }

    /** GoSafe analog for executors/threads: wraps body so a thrown Exception is
     *  captured under a per-name fingerprint task:&lt;name&gt; (mirror
     *  go.task:&lt;name&gt;), then swallowed - fire-and-forget, like GoSafe. Use
     *  with {@code executor.submit(safeRunnable(name, body))}. Catches Exception,
     *  not Throwable: an unrecoverable Error propagates uncaught. */
    public static Runnable safeRunnable(String name, Runnable body) {
        return () -> {
            try {
                body.run();
            } catch (Exception e) {
                log.log(Level.ERROR, "background task '" + name + "' failed", e);
                shipTaskError(name, e);
            }
        };
    }

    /** GoSafe analog for value-returning tasks: reports a thrown Exception under
     *  task:&lt;name&gt; then swallows it (like GoSafe), yielding Optional.empty().
     *  It does NOT rethrow - report + rethrow is a double-capture the spec forbids
     *  (JV006), since an upstream handler would report the same failure again. A
     *  caller that must observe the exception should not wrap: it should catch and
     *  call error() at its own single capture site. Errors propagate uncaught. */
    public static <T> Callable<Optional<T>> safeCallable(String name, Callable<T> body) {
        return () -> {
            try {
                return Optional.ofNullable(body.call());
            } catch (Exception e) {
                log.log(Level.ERROR, "background task '" + name + "' failed", e);
                shipTaskError(name, e);
                return Optional.empty();
            }
        };
    }

    // shipTaskError mirrors go/report's reportTaskErr: a library primitive that
    // builds the per-name fingerprint directly (task:<name>). The local log is
    // emitted at the catch site (before this ship), preserving log-before-drop.
    private static void shipTaskError(String name, Throwable t) {
        if (!ready || shouldDrop("task:" + name)) {
            return;
        }
        capture("background task failed", t, Map.of("task", name), "task:" + name, SentryLevel.ERROR);
    }

    // --- installers: uncaught handler + executor wrapper --------------------
    // The deferred goSafe surface for the executor/thread world. The wrappers
    // reuse the same report-and-swallow core (task:<name>) and per-name panic
    // fingerprints (panic:<name>) as safeRunnable/panic, so nothing new about
    // grouping or rate-limiting is introduced here.

    private static final Object installLock = new Object();
    private static volatile Thread.UncaughtExceptionHandler ourUncaughtHandler;
    private static volatile Thread.UncaughtExceptionHandler priorUncaughtHandler;

    /** Route every thread's uncaught throwable through panic(threadName, t),
     *  fingerprint panic:&lt;threadName&gt; (D002). Idempotent: a second call
     *  while installed is a no-op, never a double-wrap. Restorable: the handler
     *  present at install time is chained after our capture and restored by
     *  uninstallUncaughtHandler(), so a pre-existing handler is never lost. */
    public static void installUncaughtHandler() {
        synchronized (installLock) {
            Thread.UncaughtExceptionHandler current = Thread.getDefaultUncaughtExceptionHandler();
            if (ourUncaughtHandler != null && current == ourUncaughtHandler) {
                return;
            }
            Thread.UncaughtExceptionHandler prior = current;
            priorUncaughtHandler = prior;
            Thread.UncaughtExceptionHandler handler = (thread, throwable) -> {
                panic(thread.getName(), throwable);
                if (prior != null) {
                    prior.uncaughtException(thread, throwable);
                }
            };
            ourUncaughtHandler = handler;
            Thread.setDefaultUncaughtExceptionHandler(handler);
        }
    }

    /** Restore the default uncaught handler present before installUncaughtHandler().
     *  No-op when ours is not the current default. */
    public static void uninstallUncaughtHandler() {
        synchronized (installLock) {
            if (ourUncaughtHandler == null) {
                return;
            }
            if (Thread.getDefaultUncaughtExceptionHandler() == ourUncaughtHandler) {
                Thread.setDefaultUncaughtExceptionHandler(priorUncaughtHandler);
            }
            ourUncaughtHandler = null;
            priorUncaughtHandler = null;
        }
    }

    /** Wrap an ExecutorService so every task it runs is captured under
     *  task:&lt;name&gt; (report-and-swallow, like safeRunnable) instead of
     *  vanishing into an unobserved Future. execute + submit are wrapped;
     *  invokeAll / invokeAny delegate unwrapped - they hand results (and
     *  exceptions) straight back to the caller, who captures at their own single
     *  site, so wrapping them would double-capture (JV006). Double-wrapping a
     *  safeRunnable is safe: the inner catch fires first, so the outer never
     *  re-captures. */
    public static ExecutorService wrap(String name, ExecutorService delegate) {
        return new CapturingExecutorService(name, delegate);
    }

    // The Callable path for the executor wrapper: unlike public safeCallable
    // (Callable<Optional<T>>), it must keep the ExecutorService contract's
    // Future<T>, so a captured failure yields null rather than Optional.empty().
    // Same report-and-swallow core; the local log stays at the catch site.
    private static <T> Callable<T> guardedCallable(String name, Callable<T> body) {
        return () -> {
            try {
                return body.call();
            } catch (Exception e) {
                log.log(Level.ERROR, "background task '" + name + "' failed", e);
                shipTaskError(name, e);
                return null;
            }
        };
    }

    private static final class CapturingExecutorService implements ExecutorService {
        private final String name;
        private final ExecutorService delegate;

        CapturingExecutorService(String name, ExecutorService delegate) {
            this.name = name;
            this.delegate = delegate;
        }

        @Override
        public void execute(Runnable command) {
            delegate.execute(safeRunnable(name, command));
        }

        @Override
        public Future<?> submit(Runnable task) {
            return delegate.submit(safeRunnable(name, task));
        }

        @Override
        public <T> Future<T> submit(Runnable task, T result) {
            return delegate.submit(safeRunnable(name, task), result);
        }

        @Override
        public <T> Future<T> submit(Callable<T> task) {
            return delegate.submit(guardedCallable(name, task));
        }

        @Override
        public void shutdown() {
            delegate.shutdown();
        }

        @Override
        public List<Runnable> shutdownNow() {
            return delegate.shutdownNow();
        }

        @Override
        public boolean isShutdown() {
            return delegate.isShutdown();
        }

        @Override
        public boolean isTerminated() {
            return delegate.isTerminated();
        }

        @Override
        public boolean awaitTermination(long timeout, TimeUnit unit) throws InterruptedException {
            return delegate.awaitTermination(timeout, unit);
        }

        @Override
        public <T> List<Future<T>> invokeAll(Collection<? extends Callable<T>> tasks)
                throws InterruptedException {
            return delegate.invokeAll(tasks);
        }

        @Override
        public <T> List<Future<T>> invokeAll(Collection<? extends Callable<T>> tasks,
                long timeout, TimeUnit unit) throws InterruptedException {
            return delegate.invokeAll(tasks, timeout, unit);
        }

        @Override
        public <T> T invokeAny(Collection<? extends Callable<T>> tasks)
                throws InterruptedException, ExecutionException {
            return delegate.invokeAny(tasks);
        }

        @Override
        public <T> T invokeAny(Collection<? extends Callable<T>> tasks, long timeout, TimeUnit unit)
                throws InterruptedException, ExecutionException, TimeoutException {
            return delegate.invokeAny(tasks, timeout, unit);
        }
    }

    private static void capture(String msg, Throwable cause, Map<String, String> tags,
            String dedupKey, SentryLevel level) {
        // Preserve the original throwable's type/stack; synthesize when null.
        Throwable t = (cause != null) ? cause : new RuntimeException(msg);
        Sentry.captureException(t, scope -> {
            scope.setLevel(level);
            if (dedupKey != null && !dedupKey.isEmpty()) {
                scope.setFingerprint(List.of(dedupKey));
            }
            if (tags != null) {
                tags.forEach(scope::setTag);
            }
            scope.setContexts("tackbox", Map.of("msg", msg != null ? msg : ""));
        });
    }

    // logAt emits one local line before the readiness/rate-limit checks.
    // dedupKey is left out on purpose: it routes the event, it is not diagnostics.
    private static void logAt(Level level, String msg, Throwable cause, Map<String, String> tags) {
        String line = (tags == null || tags.isEmpty()) ? msg : msg + " " + formatTags(tags);
        if (cause != null) {
            log.log(level, line, cause);
        } else {
            log.log(level, line);
        }
    }

    private static String formatTags(Map<String, String> tags) {
        StringBuilder sb = new StringBuilder("tags={");
        boolean first = true;
        for (Map.Entry<String, String> e : new TreeMap<>(tags).entrySet()) {
            if (!first) {
                sb.append(", ");
            }
            sb.append(e.getKey()).append('=').append(e.getValue());
            first = false;
        }
        return sb.append('}').toString();
    }

    private static boolean shouldDrop(String key) {
        if (key == null || key.isEmpty()) {
            return false;
        }
        long now = System.currentTimeMillis();
        Long prev = lastSent.get(key);
        if (prev != null && now - prev < rateWindow) {
            return true;
        }
        lastSent.put(key, now);
        return false;
    }

    private static String maskDsn(String dsn) {
        try {
            URI u = URI.create(dsn);
            if (u.getHost() == null) {
                return "<malformed>";
            }
            return u.getHost() + (u.getPath() != null ? u.getPath() : "");
        } catch (RuntimeException e) {
            // no-report: malformed DSN is config; the opaque marker is the recovery
            return "<malformed>";
        }
    }

    /** Test-only: reset process-wide capture state between tests. */
    static void resetForTest() {
        uninstallUncaughtHandler();
        Sentry.close();
        ready = false;
        rateWindow = 60_000;
        flushTimeout = 2_000;
        lastSent.clear();
        log = System.getLogger("nl.tsym.tackbox.report");
    }
}

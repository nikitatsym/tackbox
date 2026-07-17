package nl.tsym.tackbox.report;

import io.sentry.Breadcrumb;
import io.sentry.Sentry;
import io.sentry.SentryLevel;
import java.lang.System.Logger;
import java.lang.System.Logger.Level;
import java.net.URI;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Consumer;

/**
 * Direct error-reporting helpers for Java applications using Sentry or GlitchTip.
 *
 * <p>Shared runtime contract:
 * <a href="https://github.com/nikitatsym/tackbox/blob/main/docs/report-contracts.md">docs/report-contracts.md</a>.
 */
public final class Report {

    private static volatile boolean ready = false;
    private static volatile long rateWindow = 60_000;
    private static volatile long flushTimeout = 2_000;
    private static final ConcurrentHashMap<String, Long> lastSent = new ConcurrentHashMap<>();
    private static volatile Logger log = System.getLogger("nl.tsym.tackbox.report");
    private static volatile Consumer<Notice> notifier;

    // Notice level strings carried to the user-lane sink.
    private static final String NOTICE_ERROR = "error";
    private static final String NOTICE_WARNING = "warning";
    private static final String NOTICE_NOTICE = "notice";
    private static final String NOTICE_FATAL = "fatal";

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
            log.log(Level.INFO, "report: capture flushed (delivery unconfirmed), DSN=" + maskDsn(opts.dsn));
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

    /** Ship one startup healthcheck (fingerprint report.startup) and flush.
     *  flush is void, so this cannot report a delivery failure; throws only
     *  when not initialized. */
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

    /** Register the user-lane sink; null clears it. With no notifier the user
     *  lane is a no-op (the local log and capture still run). The callback runs
     *  on the caller's thread; the app bridges to its UI thread itself. */
    public static void setNotifier(Consumer<Notice> fn) {
        notifier = fn;
    }

    private static void dispatchNotice(Notice n) {
        Consumer<Notice> fn = notifier;
        if (fn == null) {
            return;
        }
        try {
            fn.accept(n);
        } catch (Exception e) {
            // A throwing notifier must not break the caller's path or recurse
            // into the user lane: quiet-lane capture only (no notice).
            quiet("report notifier failed", e, null, "report.notifier");
        }
    }

    /** Level error: an unrecoverable failure you handle here. */
    public static void error(String msg, Throwable cause, Map<String, String> tags, String dedupKey) {
        logAt(Level.ERROR, msg, cause, tags);
        dispatchNotice(new Notice(msg, NOTICE_ERROR, tags, dedupKey, cause));
        if (!ready || shouldDrop(dedupKey)) {
            return;
        }
        capture(msg, cause, tags, dedupKey, SentryLevel.ERROR);
    }

    /** Level warning: a transient or external fault you recovered from. */
    public static void warn(String msg, Throwable cause, Map<String, String> tags, String dedupKey) {
        logAt(Level.WARNING, msg, cause, tags);
        dispatchNotice(new Notice(msg, NOTICE_WARNING, tags, dedupKey, cause));
        if (!ready || shouldDrop(dedupKey)) {
            return;
        }
        capture(msg, cause, tags, dedupKey, SentryLevel.WARNING);
    }

    /** Capture without the user lane, for a self-healed or
     *  degraded-with-fallback failure. */
    public static void quiet(String msg, Throwable cause, Map<String, String> tags, String dedupKey) {
        logAt(Level.WARNING, msg, cause, tags);
        if (!ready || shouldDrop(dedupKey)) {
            return;
        }
        capture(msg, cause, tags, dedupKey, SentryLevel.WARNING);
    }

    /** Feed only the user lane, no capture, for an expected environmental fault.
     *  cause is the caught error the notice is about. */
    public static void notify(String msg, Throwable cause, Map<String, String> tags, String dedupKey) {
        logAt(Level.WARNING, msg, cause, tags);
        dispatchNotice(new Notice(msg, NOTICE_NOTICE, tags, dedupKey, cause));
    }

    /** Level fatal, per-name fingerprint panic:&lt;name&gt; (D002), feeds the
     *  user lane. Pass the caught Throwable (or any recovered value) from a
     *  last-resort handler. System.Logger has no FATAL, so the local line is
     *  ERROR; the shipped event carries level FATAL. */
    public static void panic(String name, Object recovered) {
        Throwable t = (recovered instanceof Throwable th)
                ? th
                : new RuntimeException("panic in " + name + ": " + recovered);
        log.log(Level.ERROR, "panic in " + name, t);
        String key = "panic:" + name;
        dispatchNotice(new Notice("panic in " + name, NOTICE_FATAL, Map.of("source", name), key, t));
        if (!ready || shouldDrop(key)) {
            return;
        }
        Sentry.captureException(t, scope -> {
            scope.setLevel(SentryLevel.FATAL);
            scope.setTag("source", name);
            scope.setFingerprint(List.of(key));
        });
    }

    /** A breadcrumb toward the next capture, not itself an event; capture-only,
     *  no local line. */
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
        Sentry.close();
        ready = false;
        rateWindow = 60_000;
        flushTimeout = 2_000;
        lastSent.clear();
        notifier = null;
        log = System.getLogger("nl.tsym.tackbox.report");
    }
}

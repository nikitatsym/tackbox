package nl.tsym.tackbox.report;

import io.sentry.SentryOptions;
import java.lang.System.Logger;

/** Init config, mirroring go/report's Options struct. Fluent setters return
 *  this for one-liner construction; every field has a working default. */
public final class Options {

    /** Empty DSN makes every capture a log-only no-op. */
    public String dsn = "";

    /** Stamped on every event. */
    public String release;
    public String environment;

    /** Wait budget for flush(). Default 2s. */
    public long flushTimeoutMillis = 2_000;

    /** Per-dedupKey suppression window. Default 60s. */
    public long rateWindowMillis = 60_000;

    /** Pipe sentry transport diagnostics to the SDK debug logger. */
    public boolean debug = false;

    /** Send one startup healthcheck (fingerprint report.startup) and flush. */
    public boolean verify = false;

    /** Timeout for the verify healthcheck flush. Default 3s. */
    public long verifyTimeoutMillis = 3_000;

    /** Suppress the WARN log emitted on an empty DSN. */
    public boolean silentMissing = false;

    /** Local log sink. Null uses System.getLogger("nl.tsym.tackbox.report").
     *  Every capture logs here before it ships, so log-only mode (empty DSN)
     *  keeps the full msg + tags context. */
    public Logger logger;

    /** Optional event hook applied to every outgoing event (scope already
     *  merged): scrub fields here, or return null to drop. Mirrors sentry's
     *  beforeSend. Left null in production means no transform. */
    public SentryOptions.BeforeSendCallback beforeSend;

    public Options dsn(String v) { this.dsn = v; return this; }
    public Options release(String v) { this.release = v; return this; }
    public Options environment(String v) { this.environment = v; return this; }
    public Options flushTimeoutMillis(long v) { this.flushTimeoutMillis = v; return this; }
    public Options rateWindowMillis(long v) { this.rateWindowMillis = v; return this; }
    public Options debug(boolean v) { this.debug = v; return this; }
    public Options verify(boolean v) { this.verify = v; return this; }
    public Options verifyTimeoutMillis(long v) { this.verifyTimeoutMillis = v; return this; }
    public Options silentMissing(boolean v) { this.silentMissing = v; return this; }
    public Options logger(Logger v) { this.logger = v; return this; }
    public Options beforeSend(SentryOptions.BeforeSendCallback v) { this.beforeSend = v; return this; }
}

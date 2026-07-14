// Package report wraps sentry-go with the helper API from the
// error-reporting-and-coverage spec. Empty DSN = log-only no-op.
package report

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"runtime/debug"
	"sort"
	"sync"
	"time"

	"github.com/getsentry/sentry-go"
	sentryhttp "github.com/getsentry/sentry-go/http"
)

type Options struct {
	DSN          string
	Release      string
	Environment  string
	FlushTimeout time.Duration // default 2s
	RateWindow   time.Duration // default 60s
	// Debug pipes sentry-go's transport diagnostics to stderr.
	// Without it the SDK silently drops events that failed to ship.
	Debug bool
	// Verify sends a single healthcheck event (fingerprint
	// "report.startup") and blocks on Flush. Errors when delivery
	// times out. Glitchtip groups all healthchecks into one issue,
	// so this never spams.
	Verify        bool
	VerifyTimeout time.Duration // default 3s
	// SilentMissing suppresses the WARN log on empty DSN.
	SilentMissing bool
	// Logger overrides the local log sink. Nil uses a JSON handler on
	// stderr. Every capture logs here before it ships, so log-only mode
	// (empty DSN) keeps the full msg + tags context.
	Logger *slog.Logger
}

var (
	ready        bool
	httpMW       func(http.Handler) http.Handler
	rateWindow   = 60 * time.Second
	flushTimeout = 2 * time.Second
	lastSent     sync.Map
	logger       = newJSONLogger(os.Stderr)
)

// levelFatal sits above slog's ERROR; renameFatalLevel prints it FATAL.
const levelFatal = slog.LevelError + 4

func newJSONLogger(w io.Writer) *slog.Logger {
	return slog.New(slog.NewJSONHandler(w, &slog.HandlerOptions{
		Level:       slog.LevelDebug,
		ReplaceAttr: renameFatalLevel,
	}))
}

func renameFatalLevel(_ []string, a slog.Attr) slog.Attr {
	if a.Key == slog.LevelKey {
		if lv, ok := a.Value.Any().(slog.Level); ok && lv == levelFatal {
			return slog.String(slog.LevelKey, "FATAL")
		}
	}
	return a
}

func Init(opts Options) error {
	if opts.Logger != nil {
		logger = opts.Logger
	}
	if opts.DSN == "" {
		if !opts.SilentMissing {
			logger.Warn("report: DSN unset, capture disabled",
				slog.String("hint", "set SENTRY_DSN or GLITCHTIP_DSN"))
		}
		return nil
	}
	if err := sentry.Init(sentry.ClientOptions{
		Dsn:         opts.DSN,
		Release:     opts.Release,
		Environment: opts.Environment,
		Debug:       opts.Debug,
	}); err != nil {
		return fmt.Errorf("report.Init sentry: %w", err)
	}
	ready = true
	if opts.RateWindow > 0 {
		rateWindow = opts.RateWindow
	}
	if opts.FlushTimeout > 0 {
		flushTimeout = opts.FlushTimeout
	}
	httpMW = sentryhttp.New(sentryhttp.Options{
		Repanic:         true,
		WaitForDelivery: false,
		Timeout:         2 * time.Second,
	}).Handle

	if opts.Verify {
		timeout := opts.VerifyTimeout
		if timeout <= 0 {
			timeout = 3 * time.Second
		}
		if err := Verify(timeout); err != nil {
			return fmt.Errorf("report.Init verify: %w", err)
		}
		logger.Info("report: capture verified", slog.String("dsn", maskDSN(opts.DSN)))
		return nil
	}
	logger.Info("report: capture enabled, unverified", slog.String("dsn", maskDSN(opts.DSN)))
	return nil
}

func Ready() bool { return ready }

// Verify ships one healthcheck event and waits for Flush.
func Verify(timeout time.Duration) error {
	if !ready {
		return errors.New("report.Verify: not initialized")
	}
	hub := sentry.CurrentHub().Clone()
	hub.WithScope(func(scope *sentry.Scope) {
		scope.SetLevel(sentry.LevelInfo)
		scope.SetFingerprint([]string{"report.startup"})
		scope.SetTag("healthcheck", "true")
		hub.CaptureMessage("report.Verify")
	})
	if !sentry.Flush(timeout) {
		return errors.New("report.Verify: flush timeout, endpoint unreachable or rejecting")
	}
	return nil
}

func Flush(timeout ...time.Duration) {
	if !ready {
		return
	}
	d := flushTimeout
	if len(timeout) > 0 && timeout[0] > 0 {
		d = timeout[0]
	}
	sentry.Flush(d)
}

// Notice level strings, carried to the user-lane sink (SetNotifier).
const (
	noticeError   = "error"
	noticeWarning = "warning"
	noticeNotice  = "notice"
	noticeFatal   = "fatal"
)

// Error captures an unrecoverable failure handled here: local log + user lane +
// error-level capture.
func Error(ctx context.Context, msg string, err error, tags map[string]string, dedupKey string) {
	emit(ctx, slog.LevelError, sentry.LevelError, noticeError, msg, err, tags, dedupKey)
}

// Warn captures a transient or external fault you recovered from: local log +
// user lane + warning-level capture.
func Warn(ctx context.Context, msg string, err error, tags map[string]string, dedupKey string) {
	emit(ctx, slog.LevelWarn, sentry.LevelWarning, noticeWarning, msg, err, tags, dedupKey)
}

// Quiet captures without the user lane: local log + warning-level capture, no
// notice. For background / self-healed / degraded-with-fallback failures where
// anything error-severe would deserve user visibility.
func Quiet(ctx context.Context, msg string, err error, tags map[string]string, dedupKey string) {
	emit(ctx, slog.LevelWarn, sentry.LevelWarning, "", msg, err, tags, dedupKey)
}

// Notify feeds only the user lane: local log + a 'notice'-level notice, no
// capture and no rate-limit state touched, so a following Error/Warn with the
// same dedupKey still captures. For an expected environmental fault (the user
// lost connectivity). err is the caught error the notice is about.
func Notify(ctx context.Context, msg string, err error, tags map[string]string, dedupKey string) {
	logAt(ctx, slog.LevelWarn, msg, err, tags)
	dispatchNotice(Notice{Msg: msg, Level: noticeNotice, Tags: tags, DedupKey: dedupKey, Cause: err})
}

// emit is the shared lane router for Error/Warn/Quiet and the task-failure
// paths. The local log runs first; the user lane (D005) is dispatched
// unconditionally before the init+rate gate when noticeLevel is non-empty;
// capture runs last, behind that gate. key is both the rate-limit bucket and
// the Sentry fingerprint - the public verbs pass a literal dedupKey, the
// GoSafe wrapper passes a per-name key it builds directly.
func emit(ctx context.Context, logLevel slog.Level, level sentry.Level, noticeLevel, msg string, err error, tags map[string]string, key string) {
	logAt(ctx, logLevel, msg, err, tags)
	if noticeLevel != "" {
		dispatchNotice(Notice{Msg: msg, Level: noticeLevel, Tags: tags, DedupKey: key, Cause: err})
	}
	if !ready || shouldDrop(key) {
		return
	}
	capture(ctx, msg, err, tags, key, level)
}

// Notice is one user-lane event handed to the registered notifier. The app
// owns rendering and dedup/coalescing (keyed on DedupKey); the helper never
// suppresses the user lane (D005).
type Notice struct {
	Msg      string
	Level    string
	Tags     map[string]string
	DedupKey string
	Cause    error
}

var (
	notifierMu sync.RWMutex
	notifier   func(Notice)
)

// SetNotifier registers the user-lane sink; a nil fn clears it. Safe for
// concurrent registration. With no notifier the user lane is a no-op (the local
// log and capture still run). The callback runs on the caller's goroutine.
func SetNotifier(fn func(Notice)) {
	notifierMu.Lock()
	notifier = fn
	notifierMu.Unlock()
}

func dispatchNotice(n Notice) {
	notifierMu.RLock()
	fn := notifier
	notifierMu.RUnlock()
	if fn == nil {
		return
	}
	defer func() {
		if rec := recover(); rec != nil {
			// A throwing notifier must not break the caller's path or recurse
			// into the user lane: quiet-lane capture only (no notice).
			Quiet(context.Background(), "report notifier failed", asError(rec), nil, "report.notifier")
		}
	}()
	fn(n)
}

// Option configures a background-task wrapper (GoSafe) or Panic. Silent() opts
// a task's failure out of the user lane (telemetry only); the default surfaces.
type Option func(*taskOptions)

type taskOptions struct{ silent bool }

func Silent() Option { return func(o *taskOptions) { o.silent = true } }

func asError(v any) error {
	if err, ok := v.(error); ok {
		return err
	}
	return fmt.Errorf("%v", v)
}

// Panic captures a recovered panic at fatal level under the per-name
// fingerprint panic:<name>. By default it also feeds the user lane; Silent()
// (used by a quiet background task) opts out of the notice.
func Panic(name string, recovered any, opts ...Option) {
	o := taskOptions{}
	for _, opt := range opts {
		opt(&o)
	}
	logger.LogAttrs(context.Background(), levelFatal, "panic in "+name,
		slog.Any("recovered", recovered),
		slog.String("stack", string(debug.Stack())))
	key := "panic:" + name
	if !o.silent {
		dispatchNotice(Notice{Msg: "panic in " + name, Level: noticeFatal,
			Tags: map[string]string{"source": name}, DedupKey: key, Cause: asError(recovered)})
	}
	if !ready || shouldDrop(key) {
		return
	}
	hub := sentry.CurrentHub().Clone()
	hub.WithScope(func(scope *sentry.Scope) {
		scope.SetTag("source", name)
		scope.SetFingerprint([]string{key})
		scope.SetLevel(sentry.LevelFatal)
		hub.CaptureException(fmt.Errorf("panic in %s: %v", name, recovered))
	})
}

func Crumb(category, message string, data map[string]any) {
	if !ready {
		return
	}
	sentry.AddBreadcrumb(&sentry.Breadcrumb{
		Category:  category,
		Message:   message,
		Data:      data,
		Level:     sentry.LevelInfo,
		Timestamp: time.Now(),
	})
}

// GoSafe runs fn in a goroutine wrapped by recover; panics and returned
// errors are captured, each under a per-name fingerprint (panic:<name>,
// go.task:<name>) so goroutines group and rate-limit independently. By default
// a failure also surfaces to the user lane; Silent() routes it telemetry-only.
func GoSafe(name string, fn func() error, opts ...Option) {
	o := taskOptions{}
	for _, opt := range opts {
		opt(&o)
	}
	go func() {
		defer func() {
			if rec := recover(); rec != nil {
				Panic(name, rec, opts...)
			}
		}()
		reportTaskErr(name, fn(), o.silent)
	}()
}

// reportTaskErr captures a background task's returned error under a per-name
// fingerprint (go.task:<name>), mirroring Panic's per-name grouping. A library
// primitive builds this key directly; the literal-dedupKey rule governs app
// call sites, not the wrapper. A silent task captures telemetry-only at warning
// (the quiet lane); otherwise error-level plus the user lane. No-op on nil.
func reportTaskErr(name string, err error, silent bool) {
	if err == nil {
		return
	}
	logLevel, level, notice := slog.LevelError, sentry.LevelError, noticeError
	if silent {
		logLevel, level, notice = slog.LevelWarn, sentry.LevelWarning, ""
	}
	emit(context.Background(), logLevel, level, notice, "background task failed", err,
		map[string]string{"task": name}, "go.task:"+name)
}

// WrapHandler returns h with recover+capture; falls back to a
// minimal recover when Init was not called.
func WrapHandler(name string, h http.Handler) http.Handler {
	if httpMW != nil {
		return httpMW(h)
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				Panic("http."+name, rec)
				http.Error(w, "internal server error", http.StatusInternalServerError)
			}
		}()
		h.ServeHTTP(w, r)
	})
}

func DSNFromEnv() string {
	if v := os.Getenv("SENTRY_DSN"); v != "" {
		return v
	}
	return os.Getenv("GLITCHTIP_DSN")
}

// logAt emits one structured line to the local sink. dedupKey is left
// out on purpose: it routes the Sentry event, it is not diagnostics.
func logAt(ctx context.Context, level slog.Level, msg string, err error, tags map[string]string) {
	attrs := make([]slog.Attr, 0, 2)
	// no-report: this is the local log sink; err is emitted here, not handled
	if err != nil {
		attrs = append(attrs, slog.String("err", err.Error()))
	}
	if len(tags) > 0 {
		attrs = append(attrs, tagsGroup(tags))
	}
	logger.LogAttrs(ctx, level, msg, attrs...)
}

func tagsGroup(tags map[string]string) slog.Attr {
	keys := make([]string, 0, len(tags))
	for k := range tags {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	attrs := make([]any, 0, len(keys))
	for _, k := range keys {
		attrs = append(attrs, slog.String(k, tags[k]))
	}
	return slog.Group("tags", attrs...)
}

func capture(_ context.Context, msg string, err error, tags map[string]string, dedupKey string, level sentry.Level) {
	if err == nil {
		err = errors.New(msg)
	}
	hub := sentry.CurrentHub().Clone()
	hub.WithScope(func(scope *sentry.Scope) {
		scope.SetLevel(level)
		if dedupKey != "" {
			scope.SetFingerprint([]string{dedupKey})
		}
		for k, v := range tags {
			scope.SetTag(k, v)
		}
		hub.CaptureException(fmt.Errorf("%s: %w", msg, err))
	})
}

func shouldDrop(key string) bool {
	if key == "" {
		return false
	}
	now := time.Now()
	if prev, ok := lastSent.Load(key); ok {
		if now.Sub(prev.(time.Time)) < rateWindow {
			return true
		}
	}
	lastSent.Store(key, now)
	return false
}

// maskDSN returns host+project for logs (drops the secret key).
func maskDSN(dsn string) string {
	// parse-skip: user-input
	u, err := url.Parse(dsn)
	if err != nil || u.Host == "" {
		return "<malformed>"
	}
	return u.Host + u.Path
}

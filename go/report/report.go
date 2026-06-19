// Package report wraps sentry-go with the helper API from the
// error-reporting-and-coverage spec. Empty DSN = log-only no-op.
package report

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"os"
	"runtime/debug"
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
}

var (
	ready        bool
	httpMW       func(http.Handler) http.Handler
	rateWindow   = 60 * time.Second
	flushTimeout = 2 * time.Second
	lastSent     sync.Map
)

func Init(opts Options) error {
	if opts.DSN == "" {
		if !opts.SilentMissing {
			log.Printf("WARN report: DSN unset, capture disabled (set SENTRY_DSN or GLITCHTIP_DSN to enable)")
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
		log.Printf("report: capture verified, DSN=%s", maskDSN(opts.DSN))
		return nil
	}
	log.Printf("report: capture enabled (unverified), DSN=%s", maskDSN(opts.DSN))
	return nil
}

func Ready() bool { return ready }

// Verify ships one healthcheck event and waits for Flush.
func Verify(timeout time.Duration) error {
	if !ready {
		return errors.New("report.Verify: not initialized")
	}
	sentry.WithScope(func(scope *sentry.Scope) {
		scope.SetLevel(sentry.LevelInfo)
		scope.SetFingerprint([]string{"report.startup"})
		scope.SetTag("healthcheck", "true")
		sentry.CaptureMessage("report.Verify")
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

func SentryErr(ctx context.Context, msg string, err error, tags map[string]string, dedupKey string) {
	log.Printf("ERROR %s: %v", msg, err)
	if !ready || shouldDrop(dedupKey) {
		return
	}
	capture(ctx, msg, err, tags, dedupKey, sentry.LevelError)
}

func Warn(ctx context.Context, msg string, err error, tags map[string]string, dedupKey string) {
	log.Printf("WARN %s: %v", msg, err)
	if !ready || shouldDrop(dedupKey) {
		return
	}
	capture(ctx, msg, err, tags, dedupKey, sentry.LevelWarning)
}

func Panic(name string, recovered any) {
	log.Printf("FATAL panic in %s: %v\n%s", name, recovered, debug.Stack())
	if !ready {
		return
	}
	key := "panic:" + name
	if shouldDrop(key) {
		return
	}
	sentry.WithScope(func(scope *sentry.Scope) {
		scope.SetTag("source", name)
		scope.SetFingerprint([]string{key})
		scope.SetLevel(sentry.LevelFatal)
		sentry.CaptureException(fmt.Errorf("panic in %s: %v", name, recovered))
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

// GoSafe runs fn in a goroutine wrapped by recover; panics and
// returned errors are captured.
func GoSafe(name string, fn func() error) {
	go func() {
		defer func() {
			if rec := recover(); rec != nil {
				Panic(name, rec)
			}
		}()
		if err := fn(); err != nil {
			SentryErr(context.Background(), "background task failed", err,
				map[string]string{"task": name}, "go.task")
		}
	}()
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

func capture(_ context.Context, msg string, err error, tags map[string]string, dedupKey string, level sentry.Level) {
	if err == nil {
		err = errors.New(msg)
	}
	sentry.WithScope(func(scope *sentry.Scope) {
		scope.SetLevel(level)
		if dedupKey != "" {
			scope.SetFingerprint([]string{dedupKey})
		}
		for k, v := range tags {
			scope.SetTag(k, v)
		}
		sentry.CaptureException(fmt.Errorf("%s: %w", msg, err))
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

package report

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/getsentry/sentry-go"
)

// With an empty DSN the local JSON line is the only record, so it must
// carry msg + err + tags. dedupKey must not leak into it.
func TestLocalLogCarriesTagsWhenCaptureDisabled(t *testing.T) {
	var buf bytes.Buffer
	if err := Init(Options{Logger: newJSONLogger(&buf), SilentMissing: true}); err != nil {
		t.Fatalf("init: %v", err)
	}
	if Ready() {
		t.Fatal("empty DSN must leave capture disabled")
	}

	Error(context.Background(), "unlock failed", errors.New("bad passphrase"),
		map[string]string{"item": "work-key"}, "vault.unlock")

	rec := decodeLine(t, buf.Bytes())
	if rec["level"] != "ERROR" {
		t.Errorf("level = %v, want ERROR", rec["level"])
	}
	if rec["msg"] != "unlock failed" {
		t.Errorf("msg = %v, want unlock failed", rec["msg"])
	}
	if rec["err"] != "bad passphrase" {
		t.Errorf("err = %v, want bad passphrase", rec["err"])
	}
	tags, ok := rec["tags"].(map[string]any)
	if !ok || tags["item"] != "work-key" {
		t.Errorf("tags = %v, want item=work-key", rec["tags"])
	}
	if strings.Contains(buf.String(), "vault.unlock") {
		t.Errorf("dedupKey leaked into local log: %q", buf.String())
	}
}

func TestPanicLogsFatalLevel(t *testing.T) {
	var buf bytes.Buffer
	if err := Init(Options{Logger: newJSONLogger(&buf), SilentMissing: true}); err != nil {
		t.Fatalf("init: %v", err)
	}

	Panic("tray-loop", "boom")

	rec := decodeLine(t, buf.Bytes())
	if rec["level"] != "FATAL" {
		t.Errorf("level = %v, want FATAL", rec["level"])
	}
	if rec["recovered"] != "boom" {
		t.Errorf("recovered = %v, want boom", rec["recovered"])
	}
}

func decodeLine(t *testing.T, b []byte) map[string]any {
	t.Helper()
	var rec map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(b), &rec); err != nil {
		t.Fatalf("log line is not JSON: %v (%q)", err, string(b))
	}
	return rec
}

// recorder is a sentry.Transport that records captured events in memory. A
// custom transport is invoked synchronously by CaptureException, so the count
// is settled the moment capture returns.
type recorder struct {
	mu     sync.Mutex
	events []*sentry.Event
}

func (r *recorder) SendEvent(e *sentry.Event) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.events = append(r.events, e)
}
func (r *recorder) Flush(time.Duration) bool              { return true }
func (r *recorder) FlushWithContext(context.Context) bool { return true }
func (r *recorder) Configure(sentry.ClientOptions)        {}
func (r *recorder) Close()                                {}

func (r *recorder) count() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.events)
}

func (r *recorder) fingerprints() map[string]bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	set := make(map[string]bool, len(r.events))
	for _, e := range r.events {
		set[strings.Join(e.Fingerprint, ",")] = true
	}
	return set
}

// fingerprintCounts is a multiset: how many events carry each fingerprint.
// A scope bleed shows up as one fingerprint counted twice and another missing.
func (r *recorder) fingerprintCounts() map[string]int {
	r.mu.Lock()
	defer r.mu.Unlock()
	counts := make(map[string]int, len(r.events))
	for _, e := range r.events {
		counts[strings.Join(e.Fingerprint, ",")]++
	}
	return counts
}

func (r *recorder) reset() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.events = nil
}

// initRecorder wires a recording transport into sentry and flips report into
// the ready state with a rate window wide enough that only an explicit reset
// clears a bucket. Package-level state is restored on cleanup.
func initRecorder(t *testing.T) *recorder {
	t.Helper()
	rec := &recorder{}
	if err := sentry.Init(sentry.ClientOptions{Dsn: "http://test@localhost/1", Transport: rec}); err != nil {
		t.Fatalf("sentry init: %v", err)
	}
	prevReady, prevWindow, prevLogger := ready, rateWindow, logger
	ready = true
	rateWindow = time.Hour
	logger = newJSONLogger(io.Discard)
	resetRateLimit()
	t.Cleanup(func() {
		ready, rateWindow, logger = prevReady, prevWindow, prevLogger
		resetRateLimit()
	})
	return rec
}

func resetRateLimit() {
	lastSent.Range(func(k, _ any) bool {
		lastSent.Delete(k)
		return true
	})
}

func (r *recorder) levelOf(fp string) (sentry.Level, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, e := range r.events {
		if strings.Join(e.Fingerprint, ",") == fp {
			return e.Level, true
		}
	}
	return "", false
}

// noticeRecorder captures the user-lane notices a registered notifier receives.
type noticeRecorder struct {
	mu      sync.Mutex
	notices []Notice
}

func (n *noticeRecorder) count() int {
	n.mu.Lock()
	defer n.mu.Unlock()
	return len(n.notices)
}

func (n *noticeRecorder) at(i int) Notice {
	n.mu.Lock()
	defer n.mu.Unlock()
	return n.notices[i]
}

// useNotifier installs a recording notifier and restores the prior one on
// cleanup, so a test's user-lane state never leaks into the next.
func useNotifier(t *testing.T) *noticeRecorder {
	t.Helper()
	nr := &noticeRecorder{}
	notifierMu.RLock()
	prev := notifier
	notifierMu.RUnlock()
	SetNotifier(func(n Notice) {
		nr.mu.Lock()
		nr.notices = append(nr.notices, n)
		nr.mu.Unlock()
	})
	t.Cleanup(func() { SetNotifier(prev) })
	return nr
}

// Error feeds the user lane even with capture disabled (empty DSN): the notice
// is delivered before the readiness gate, capture stays off.
func TestReportDispatchesUserLaneEvenWhenNotReady(t *testing.T) {
	rec := initRecorder(t)
	prevReady := ready
	ready = false
	t.Cleanup(func() { ready = prevReady })
	nr := useNotifier(t)

	Error(context.Background(), "connection lost mid-stream", errors.New("boom"),
		map[string]string{"area": "net"}, "net.conn")

	if nr.count() != 1 {
		t.Fatalf("user lane must dispatch without init: got %d notices", nr.count())
	}
	if got := nr.at(0); got.Level != "error" || got.DedupKey != "net.conn" {
		t.Errorf("notice = %+v, want level=error dedupKey=net.conn", got)
	}
	if rec.count() != 0 {
		t.Errorf("capture must stay gated off when not ready: got %d events", rec.count())
	}
}

// Rate-limited: both calls reach the user lane, the second capture is dropped.
func TestReportDispatchesUserLaneWhenRateLimited(t *testing.T) {
	rec := initRecorder(t)
	nr := useNotifier(t)

	Error(context.Background(), "poll failed on stale token", errors.New("e1"), nil, "poll.stale")
	Error(context.Background(), "poll failed on stale token", errors.New("e2"), nil, "poll.stale")

	if nr.count() != 2 {
		t.Errorf("every event reaches the user lane: got %d notices, want 2", nr.count())
	}
	if rec.count() != 1 {
		t.Errorf("duplicate capture suppressed within the window: got %d events, want 1", rec.count())
	}
}

// Quiet: warning-level capture, no user lane.
func TestQuietCapturesWarningNoUserLane(t *testing.T) {
	rec := initRecorder(t)
	nr := useNotifier(t)

	Quiet(context.Background(), "cache refresh degraded, using stale", errors.New("timeout"), nil, "cache.refresh")

	if nr.count() != 0 {
		t.Errorf("quiet must not touch the user lane: got %d notices", nr.count())
	}
	if rec.count() != 1 {
		t.Fatalf("quiet must capture: got %d events, want 1", rec.count())
	}
	if lv, _ := rec.levelOf("cache.refresh"); lv != sentry.LevelWarning {
		t.Errorf("quiet capture level = %v, want warning", lv)
	}
}

// Notify feeds only the user lane, captures nothing, and touches no rate-limit
// state - so a following Error on the same dedupKey still captures.
func TestNotifyUserLaneOnlyDoesNotConsumeRateSlot(t *testing.T) {
	rec := initRecorder(t)
	nr := useNotifier(t)

	Notify(context.Background(), "you appear to be offline", errors.New("net down"), nil, "conn.offline")
	if nr.count() != 1 || nr.at(0).Level != "notice" {
		t.Fatalf("notify must dispatch one 'notice' event: got %d notices", nr.count())
	}
	if rec.count() != 0 {
		t.Fatalf("notify must not capture: got %d events", rec.count())
	}

	// Same dedupKey: proves notify did not consume the capture's rate slot.
	Error(context.Background(), "still offline after retry", errors.New("net down"), nil, "conn.offline")
	if rec.count() != 1 {
		t.Errorf("following Error on the notify dedupKey must still capture: got %d events", rec.count())
	}
	if nr.count() != 2 {
		t.Errorf("Error also reaches the user lane: got %d notices, want 2", nr.count())
	}
}

// Panic feeds the user lane at fatal by default; Silent() (the quiet task
// opt-out) captures the panic but skips the user lane.
func TestPanicDefaultUserLaneAndSilentOptOut(t *testing.T) {
	rec := initRecorder(t)
	nr := useNotifier(t)

	Panic("tray-loop", "boom")
	Panic("indexer", "boom", Silent())

	if nr.count() != 1 {
		t.Fatalf("only the default panic feeds the user lane: got %d notices, want 1", nr.count())
	}
	if got := nr.at(0); got.Level != "fatal" || got.DedupKey != "panic:tray-loop" || got.Msg != "panic in tray-loop" {
		t.Errorf("panic notice = %+v, want fatal panic:tray-loop 'panic in tray-loop'", got)
	}
	if rec.count() != 2 {
		t.Errorf("both panics capture (per-name): got %d events, want 2", rec.count())
	}
}

// A quiet background task captures at warning with no user lane; a loud one
// captures at error and feeds the user lane. Asserted through reportTaskErr,
// the synchronous core GoSafe (with/without Silent) drives.
func TestReportTaskErrSilentRoutesQuiet(t *testing.T) {
	rec := initRecorder(t)
	nr := useNotifier(t)

	reportTaskErr("quiet-task", errors.New("boom"), true)
	reportTaskErr("loud-task", errors.New("boom"), false)

	if nr.count() != 1 || nr.at(0).DedupKey != "go.task:loud-task" {
		t.Errorf("only the loud task feeds the user lane: got %d notices", nr.count())
	}
	if lv, _ := rec.levelOf("go.task:quiet-task"); lv != sentry.LevelWarning {
		t.Errorf("quiet task capture level = %v, want warning", lv)
	}
	if lv, _ := rec.levelOf("go.task:loud-task"); lv != sentry.LevelError {
		t.Errorf("loud task capture level = %v, want error", lv)
	}
}

// A throwing notifier must not break the caller's path or recurse: the failure
// is captured on the quiet lane and the original event still ships.
func TestNotifierPanicDoesNotBreakCaller(t *testing.T) {
	rec := initRecorder(t)
	prevNotifier := func(Notice) {}
	notifierMu.RLock()
	prevNotifier = notifier
	notifierMu.RUnlock()
	SetNotifier(func(Notice) { panic("notifier is broken") })
	t.Cleanup(func() { SetNotifier(prevNotifier) })

	// Returns normally: if the notifier panic propagated, this test would crash.
	Error(context.Background(), "upload failed mid-flight", errors.New("hangup"), nil, "upload.fail")

	fps := rec.fingerprints()
	if !fps["upload.fail"] {
		t.Errorf("original event must still capture after a notifier panic; got %v", fps)
	}
	if !fps["report.notifier"] {
		t.Errorf("the broken notifier must be captured on the quiet lane; got %v", fps)
	}
}

// GoSafe's error path fingerprints and rate-limits per task name: distinct
// names never share a bucket, the same name within the window collapses to one
// event. Asserted through the synchronous core reportTaskErr.
func TestGoSafeErrPathPerNameFingerprint(t *testing.T) {
	rec := initRecorder(t)

	reportTaskErr("alpha", errors.New("boom"), false)
	reportTaskErr("beta", errors.New("boom"), false)
	if rec.count() != 2 {
		t.Fatalf("distinct names: got %d events, want 2 (neither dropped)", rec.count())
	}
	fps := rec.fingerprints()
	for _, want := range []string{"go.task:alpha", "go.task:beta"} {
		if !fps[want] {
			t.Errorf("missing per-name fingerprint %q; got %v", want, fps)
		}
	}
	first := rec.events[0]
	if first.Level != sentry.LevelError {
		t.Errorf("level = %v, want error", first.Level)
	}
	if first.Tags["task"] != "alpha" {
		t.Errorf("tag task = %q, want alpha", first.Tags["task"])
	}

	resetRateLimit()
	rec.reset()
	reportTaskErr("gamma", errors.New("first"), false)
	reportTaskErr("gamma", errors.New("second"), false)
	if rec.count() != 1 {
		t.Fatalf("same name within window: got %d events, want 1 (second dropped)", rec.count())
	}
}

// End-to-end through the exported goroutine wrapper under real concurrency:
// many distinctly-named GoSafe tasks fail at once, released together by a gate
// to maximize overlap. Because each capture clones the hub (D003), no goroutine
// can bleed another's fingerprint, so the recorded fingerprint multiset must
// equal exactly {go.task:<name>: 1} for every launched name - each once, none
// wrong. Pre-D003 (captures on the shared global hub) this bled under overlap.
func TestGoSafeGoroutineCapturesPerName(t *testing.T) {
	rec := initRecorder(t)

	const n = 40
	want := make(map[string]int, n)
	gate := make(chan struct{})
	for i := 0; i < n; i++ {
		name := fmt.Sprintf("worker-%02d", i)
		want["go.task:"+name] = 1
		GoSafe(name, func() error {
			<-gate // hold every goroutine until all are launched, then storm
			return errors.New("boom")
		})
	}
	close(gate)

	deadline := time.Now().Add(5 * time.Second)
	for rec.count() < n && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	got := rec.fingerprintCounts()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("fingerprint multiset mismatch under concurrency (scope bleed?):\n got  %v\n want %v", got, want)
	}
}

// WrapHandler must honor the documented "panic -> 500" contract in BOTH modes.
// After Init, sentryhttp captures the panic and re-panics (Repanic:true), so
// the outer recover must turn that into a 500; before Init, the hand-rolled
// fallback recover writes it. Either way the client sees 500, never a dropped
// connection. The in-test recover only keeps a pre-fix escaped panic from
// crashing the binary so the recorder can be read as the audit read it (it
// observed the default 200/empty when the panic flew past WrapHandler).
func TestWrapHandler500Contract(t *testing.T) {
	prevReady, prevMW, prevWindow, prevLogger := ready, httpMW, rateWindow, logger
	t.Cleanup(func() {
		ready, httpMW, rateWindow, logger = prevReady, prevMW, prevWindow, prevLogger
		resetRateLimit()
	})
	logger = newJSONLogger(io.Discard)

	assert500 := func(t *testing.T, name string) {
		t.Helper()
		h := WrapHandler(name, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
			panic("boom in " + name)
		}))
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/", nil)

		var escaped any
		func() {
			defer func() { escaped = recover() }()
			h.ServeHTTP(rec, req)
		}()

		if rec.Code != http.StatusInternalServerError {
			t.Fatalf("status = %d, want 500 (panic escaped WrapHandler = %v)", rec.Code, escaped)
		}
		if !strings.Contains(rec.Body.String(), "internal server error") {
			t.Fatalf("body = %q, want to contain %q", rec.Body.String(), "internal server error")
		}
	}

	t.Run("after Init sentryhttp path", func(t *testing.T) {
		ready, httpMW = false, nil
		resetRateLimit()
		// Valid dummy DSN; Verify:false so Init never dials out, yet it still
		// installs the sentryhttp middleware and takes the post-Init branch.
		if err := Init(Options{DSN: "https://public@localhost/1", Logger: newJSONLogger(io.Discard), SilentMissing: true}); err != nil {
			t.Fatalf("init: %v", err)
		}
		if !Ready() || httpMW == nil {
			t.Fatalf("Init must enable capture and install middleware (ready=%v, mw!=nil=%v)", Ready(), httpMW != nil)
		}
		assert500(t, "api-post")
	})

	t.Run("pre-Init fallback path", func(t *testing.T) {
		ready, httpMW = false, nil
		assert500(t, "api-pre")
	})
}

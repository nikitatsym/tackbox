package report

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
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

// Panic feeds the user lane at fatal and captures per name (D002): distinct
// names never share a fingerprint bucket, and the same name inside the rate
// window collapses to one event.
func TestPanicFeedsUserLaneAndCaptures(t *testing.T) {
	rec := initRecorder(t)
	nr := useNotifier(t)

	Panic("tray-loop", "boom")
	Panic("indexer", "boom")

	if nr.count() != 2 {
		t.Fatalf("each panic feeds the user lane: got %d notices, want 2", nr.count())
	}
	if got := nr.at(0); got.Level != "fatal" || got.DedupKey != "panic:tray-loop" || got.Msg != "panic in tray-loop" {
		t.Errorf("panic notice = %+v, want fatal panic:tray-loop 'panic in tray-loop'", got)
	}
	if rec.count() != 2 {
		t.Fatalf("distinct names capture per name: got %d events, want 2", rec.count())
	}
	fps := rec.fingerprints()
	for _, want := range []string{"panic:tray-loop", "panic:indexer"} {
		if !fps[want] {
			t.Errorf("missing per-name fingerprint %q; got %v", want, fps)
		}
	}
	if lv, _ := rec.levelOf("panic:tray-loop"); lv != sentry.LevelFatal {
		t.Errorf("panic capture level = %v, want fatal", lv)
	}

	resetRateLimit()
	rec.reset()
	Panic("tray-loop", "first")
	Panic("tray-loop", "second")
	if rec.count() != 1 {
		t.Fatalf("same name within window: got %d events, want 1 (second dropped)", rec.count())
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

// A concurrent storm of direct Error captures keeps each event under its own
// fingerprint (D003): the hub is cloned per capture, so no goroutine can bleed
// another's fingerprint and the recorded multiset must be exactly one event per
// key. Each site is a fixed closure whose msg and dedupKey are literals in the
// Error call itself (ERC006 wants the literal in the AST). The raw goroutines
// are application-owned test concurrency, not a tackbox launcher.
func TestConcurrentDirectCapturesStayPerFingerprint(t *testing.T) {
	rec := initRecorder(t)
	ctx := context.Background()
	gate := make(chan struct{})

	sites := []func(){
		func() { <-gate; Error(ctx, "concurrent capture", errors.New("boom"), nil, "storm.00") },
		func() { <-gate; Error(ctx, "concurrent capture", errors.New("boom"), nil, "storm.01") },
		func() { <-gate; Error(ctx, "concurrent capture", errors.New("boom"), nil, "storm.02") },
		func() { <-gate; Error(ctx, "concurrent capture", errors.New("boom"), nil, "storm.03") },
		func() { <-gate; Error(ctx, "concurrent capture", errors.New("boom"), nil, "storm.04") },
		func() { <-gate; Error(ctx, "concurrent capture", errors.New("boom"), nil, "storm.05") },
		func() { <-gate; Error(ctx, "concurrent capture", errors.New("boom"), nil, "storm.06") },
		func() { <-gate; Error(ctx, "concurrent capture", errors.New("boom"), nil, "storm.07") },
		func() { <-gate; Error(ctx, "concurrent capture", errors.New("boom"), nil, "storm.08") },
		func() { <-gate; Error(ctx, "concurrent capture", errors.New("boom"), nil, "storm.09") },
		func() { <-gate; Error(ctx, "concurrent capture", errors.New("boom"), nil, "storm.10") },
		func() { <-gate; Error(ctx, "concurrent capture", errors.New("boom"), nil, "storm.11") },
	}
	want := map[string]int{
		"storm.00": 1, "storm.01": 1, "storm.02": 1, "storm.03": 1,
		"storm.04": 1, "storm.05": 1, "storm.06": 1, "storm.07": 1,
		"storm.08": 1, "storm.09": 1, "storm.10": 1, "storm.11": 1,
	}

	var wg sync.WaitGroup
	for _, site := range sites {
		wg.Add(1)
		go func() {
			defer wg.Done()
			site()
		}()
	}
	close(gate) // release every parked goroutine to storm together
	wg.Wait()

	got := rec.fingerprintCounts()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("fingerprint multiset mismatch under concurrency (scope bleed?):\n got  %v\n want %v", got, want)
	}
}

package report

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
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

	SentryErr(context.Background(), "unlock failed", errors.New("bad passphrase"),
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

// GoSafe's error path fingerprints and rate-limits per task name: distinct
// names never share a bucket, the same name within the window collapses to one
// event. Asserted through the synchronous core reportTaskErr.
func TestGoSafeErrPathPerNameFingerprint(t *testing.T) {
	rec := initRecorder(t)

	reportTaskErr("alpha", errors.New("boom"))
	reportTaskErr("beta", errors.New("boom"))
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
	reportTaskErr("gamma", errors.New("first"))
	reportTaskErr("gamma", errors.New("second"))
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

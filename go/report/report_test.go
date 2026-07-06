package report

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
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

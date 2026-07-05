package parsenil

import (
	"encoding/json"
	"fmt"
	"net"
	"strconv"

	"github.com/nikitatsym/tackbox/go/report"
)

// --- error-returning parsers, block form ---

func okCapture(data []byte) {
	var v map[string]any
	err := json.Unmarshal(data, &v)
	if err != nil {
		report.SentryErr("parse", "config payload", err, nil, "parse.config")
	}
}

func okSkip(data []byte) {
	var v map[string]any
	// parse-skip: optional-config
	err := json.Unmarshal(data, &v)
	_ = err
}

func violationDiscard(data []byte) {
	var v map[string]any
	_ = json.Unmarshal(data, &v) // want `ERC002:.*json.Unmarshal err discarded`
}

// F5: chain-preserving propagation of a parse error is valid handling - the
// caller reports it. Bare `return err` was a false positive before F5.
func okPropagateBare(data []byte) error {
	var v map[string]any
	err := json.Unmarshal(data, &v)
	if err != nil {
		return err
	}
	return nil
}

// %w wrap carries the parse error into the unwrap chain: propagation.
func okPropagateWrapW(data []byte) error {
	var v map[string]any
	err := json.Unmarshal(data, &v)
	if err != nil {
		return fmt.Errorf("config: %w", err)
	}
	return nil
}

// %v stringifies the parse error and breaks the chain: still requires capture.
func violationPropagateV(data []byte) error {
	var v map[string]any
	err := json.Unmarshal(data, &v)
	if err != nil { // want `ERC002:.*json.Unmarshal err-branch must capture`
		return fmt.Errorf("config: %v", err)
	}
	return nil
}

func violationSchemaDrift(data []byte) {
	var v map[string]any
	// parse-skip: schema-drift // want `ERC002:.*schema-drift.*capture instead`
	err := json.Unmarshal(data, &v)
	_ = err
}

// --- error-returning parsers, short form ---

func okShortFormCapture(s string) {
	if _, err := strconv.Atoi(s); err != nil {
		report.SentryErr("parse", "atoi", err, nil, "parse.atoi")
	}
}

func okShortFormMarker(s string) {
	// parse-skip: optional-config
	if _, err := strconv.Atoi(s); err != nil {
		_ = err
	}
}

// F5: short-form bare `return err` propagates chain-preservingly - clean.
func okShortFormPropagate(s string) error {
	if _, err := strconv.Atoi(s); err != nil {
		return err
	}
	return nil
}

// --- net.ParseIP, block form ---

func okParseIPCapture(s string) {
	v := net.ParseIP(s)
	if v == nil {
		report.SentryErr("net", "bad ip", nil, nil, "net.parseip")
	}
	_ = v
}

func okParseIPMarker(s string) {
	// parse-skip: optional-config
	v := net.ParseIP(s)
	_ = v
}

func violationParseIPDiscard(s string) {
	_ = net.ParseIP(s) // want `ERC002:.*net.ParseIP result discarded`
}

func violationParseIPNoNilCheck(s string) {
	v := net.ParseIP(s) // want `ERC002:.*net.ParseIP result .v. not nil-checked`
	_ = v
}

func violationParseIPNoCapture(s string) {
	v := net.ParseIP(s)
	if v == nil { // want `ERC002:.*net.ParseIP nil-branch must capture`
		_ = "swallowed"
	}
	_ = v
}

// --- net.ParseIP, short form ---

func okParseIPShort(s string) {
	if v := net.ParseIP(s); v == nil {
		report.SentryErr("net", "bad ip", nil, nil, "net.parseip")
		_ = v
	}
}

func violationParseIPShort(s string) {
	if v := net.ParseIP(s); v == nil { // want `ERC002:.*net.ParseIP nil-branch must capture`
		_ = v
	}
}

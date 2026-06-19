package parsenil

import (
	"encoding/json"
	"net"
	"strconv"
)

func sentryErr(area, msg string, err error, tags map[string]string, key string) {}

// --- error-returning parsers, block form ---

func okCapture(data []byte) {
	var v map[string]any
	err := json.Unmarshal(data, &v)
	if err != nil {
		sentryErr("parse", "config payload", err, nil, "parse.config")
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

func violationNoCapture(data []byte) error {
	var v map[string]any
	err := json.Unmarshal(data, &v)
	if err != nil { // want `ERC002:.*json.Unmarshal err-branch must capture`
		return err
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
		sentryErr("parse", "atoi", err, nil, "parse.atoi")
	}
}

func okShortFormMarker(s string) {
	// parse-skip: optional-config
	if _, err := strconv.Atoi(s); err != nil {
		_ = err
	}
}

func violationShortFormNoCapture(s string) error {
	if _, err := strconv.Atoi(s); err != nil { // want `ERC002:.*strconv.Atoi err-branch must capture`
		return err
	}
	return nil
}

// --- net.ParseIP, block form ---

func okParseIPCapture(s string) {
	v := net.ParseIP(s)
	if v == nil {
		sentryErr("net", "bad ip", nil, nil, "net.parseip")
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
		sentryErr("net", "bad ip", nil, nil, "net.parseip")
		_ = v
	}
}

func violationParseIPShort(s string) {
	if v := net.ParseIP(s); v == nil { // want `ERC002:.*net.ParseIP nil-branch must capture`
		_ = v
	}
}

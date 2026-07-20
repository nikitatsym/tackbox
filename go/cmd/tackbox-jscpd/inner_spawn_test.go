package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// argMaxProbe exceeds the macOS ARG_MAX (1 MiB, env included) that motivated the
// fix; a target list past it as raw inner argv is the E2BIG this test guards.
const argMaxProbe = 1 << 20

// writeJscpdStub stands in for the jscpd binary: it writes an empty report to
// --output and copies the --config file to JSCPD_STUB_RECORD so the test can
// verify which paths reached it, then exits 0. It never takes files on argv.
func writeJscpdStub(t *testing.T) string {
	t.Helper()
	stub := filepath.Join(t.TempDir(), "jscpd-stub.sh")
	script := "#!/bin/sh\n" +
		"out=\"\"; cfg=\"\"\n" +
		"while [ $# -gt 0 ]; do\n" +
		"  case \"$1\" in\n" +
		"    --output) out=\"$2\"; shift 2 ;;\n" +
		"    --config) cfg=\"$2\"; shift 2 ;;\n" +
		"    *) shift ;;\n" +
		"  esac\n" +
		"done\n" +
		"[ -n \"$out\" ] && printf '{\"duplicates\":[]}' > \"$out/jscpd-report.json\"\n" +
		"[ -n \"$cfg\" ] && cp \"$cfg\" \"$JSCPD_STUB_RECORD\"\n" +
		"exit 0\n"
	if err := os.WriteFile(stub, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return stub
}

// makeFilesExceedingArgMax creates real empty files under a deep-named dir until
// their absolute paths total more than minBytes; returns the paths. Real files
// are required - the wrapper reads each one for the DUP002 ignore-marker scan.
func makeFilesExceedingArgMax(t *testing.T, minBytes int) []string {
	t.Helper()
	seg := strings.Repeat("d", 200)
	dir := filepath.Join(t.TempDir(), seg, seg, seg, seg)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	var paths []string
	total := 0
	for i := 0; total <= minBytes; i++ {
		p := filepath.Join(dir, fmt.Sprintf("file_%08d.go", i))
		if err := os.WriteFile(p, nil, 0o644); err != nil {
			t.Fatal(err)
		}
		paths = append(paths, p)
		total += len(p) + 1
	}
	return paths
}

func TestInnerSpawnSurvivesHugeFileSetViaConfig(t *testing.T) {
	bin := buildWrapper(t)
	stub := writeJscpdStub(t)
	paths := makeFilesExceedingArgMax(t, argMaxProbe)

	total := 0
	for _, p := range paths {
		total += len(p) + 1
	}
	if total <= argMaxProbe {
		t.Fatalf("path bytes %d did not exceed probe %d", total, argMaxProbe)
	}

	listFile := filepath.Join(t.TempDir(), "list.txt")
	if err := os.WriteFile(listFile, []byte(strings.Join(paths, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	record := filepath.Join(t.TempDir(), "record.json")

	cmd := exec.Command(bin, "--jscpd="+stub, "--paths-from", listFile)
	cmd.Env = append(os.Environ(), "JSCPD_STUB_RECORD="+record)
	var stderr strings.Builder
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("wrapper inner spawn failed (E2BIG?): %v\nstderr: %s", err, stderr.String())
	}

	// The whole file set reached jscpd through the config path array.
	data, err := os.ReadFile(record)
	if err != nil {
		t.Fatalf("stub did not record a config: %v", err)
	}
	var cfg struct {
		Path []string `json:"path"`
	}
	if err := json.Unmarshal(data, &cfg); err != nil {
		t.Fatalf("recorded config is not valid json: %v\n%s", err, data)
	}
	if len(cfg.Path) != len(paths) {
		t.Fatalf("config carried %d paths, want %d (file set truncated)", len(cfg.Path), len(paths))
	}
}

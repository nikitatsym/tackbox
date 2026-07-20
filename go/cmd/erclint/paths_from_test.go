package main_test

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// The --paths-from file is injected where positional package patterns went, so
// the run is identical to passing them on argv - proven against the same golden.
func TestPathsFromInjectsPackagePatterns(t *testing.T) {
	bin := buildErclint(t, "test")
	fixture, err := filepath.Abs("testdata/goldenrepo")
	if err != nil {
		t.Fatalf("abs fixture: %v", err)
	}
	list := filepath.Join(t.TempDir(), "pkgs.txt")
	if err := os.WriteFile(list, []byte("./...\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(bin, "--json", "--paths-from", list)
	cmd.Dir = fixture
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("run --paths-from: %v\nstderr: %s", err, stderr.String())
	}
	got := strings.ReplaceAll(string(out), fixture, "<FIXTURE>")
	want := mustRead(t, "testdata/golden/json.stdout")
	if got != want {
		t.Fatalf("--paths-from stdout mismatch\n--- got:\n%s\n--- want:\n%s", got, want)
	}
}

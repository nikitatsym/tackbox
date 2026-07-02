package main_test

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestVersionFlag(t *testing.T) {
	bin := buildErclint(t, "test")
	out, err := exec.Command(bin, "--version").Output()
	if err != nil {
		t.Fatalf("run --version: %v", err)
	}
	want := mustRead(t, "testdata/golden/version.stdout")
	if string(out) != want {
		t.Fatalf("--version stdout mismatch\n got: %q\nwant: %q", out, want)
	}
}

func TestJSONPackageAttribution(t *testing.T) {
	bin := buildErclint(t, "test")
	fixture, err := filepath.Abs("testdata/goldenrepo")
	if err != nil {
		t.Fatalf("abs fixture: %v", err)
	}
	cmd := exec.Command(bin, "--json", "./...")
	cmd.Dir = fixture
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("run --json: %v\nstderr: %s", err, stderr.String())
	}
	got := strings.ReplaceAll(string(out), fixture, "<FIXTURE>")
	want := mustRead(t, "testdata/golden/json.stdout")
	if got != want {
		t.Fatalf("--json stdout mismatch\n--- got:\n%s\n--- want:\n%s", got, want)
	}
}

func buildErclint(t *testing.T, version string) string {
	t.Helper()
	dir := t.TempDir()
	bin := filepath.Join(dir, "erclint")
	cmd := exec.Command("go", "build",
		"-ldflags", "-X main.version="+version,
		"-o", bin, ".")
	cmd.Stdout = os.Stderr
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("build erclint: %v", err)
	}
	return bin
}

func mustRead(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return string(b)
}

package main_test

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"unicode"
)

const badGo = `package foo

func Bad(ctx X, msg string, err error) {
	SentryErr(ctx, msg, err)
}
`

const cleanGo = `package foo

func Good(ctx X, msg string, err error, tags T) {
	SentryErr(ctx, msg, err, tags, "area.suffix")
}
`

func TestExplicitTestsDirFileYieldsFinding(t *testing.T) {
	requireOpengrepOnPath(t)
	bin := buildOpengrepWrapper(t)
	repo := makeRepo(t)

	if err := os.MkdirAll(filepath.Join(repo, "tests"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(repo, "tests", "bad.go"), []byte(badGo), 0o644); err != nil {
		t.Fatal(err)
	}

	stdout, stderr, runErr := runWrapper(t, bin, repo, "tests/bad.go")
	if runErr == nil {
		t.Fatalf("expected non-zero exit for erc006 finding; got clean\nstdout=%s\nstderr=%s", stdout, stderr)
	}
	var exitErr *exec.ExitError
	if !errors.As(runErr, &exitErr) {
		t.Fatalf("expected opengrep exit error, got %v\nstderr=%s", runErr, stderr)
	}
	if !strings.Contains(stdout, "erc006-dedupkey-missing") {
		t.Fatalf("expected erc006-dedupkey-missing in stdout:\n%s", stdout)
	}
	if !strings.Contains(stripWhitespace(stdout), "tests/bad.go") {
		t.Fatalf("expected repo-relative path tests/bad.go in stdout:\n%s", stdout)
	}
	if strings.Contains(stdout, repo) {
		t.Fatalf("absolute repo path leaked into stdout:\n%s", stdout)
	}
	if strings.Contains(stderr, "Scan skipped") {
		t.Fatalf("opengrep skipped explicitly-passed file; builtin default-ignore still active:\n%s", stderr)
	}
}

func TestSemgrepIgnoreInRepoRootFails(t *testing.T) {
	bin := buildOpengrepWrapper(t)
	repo := makeRepo(t)

	if err := os.WriteFile(filepath.Join(repo, ".semgrepignore"), []byte("*.go\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(repo, "any.go"), []byte(cleanGo), 0o644); err != nil {
		t.Fatal(err)
	}

	stdout, stderr, runErr := runWrapper(t, bin, repo, "any.go")
	if runErr == nil {
		t.Fatalf("expected rejection when repo has .semgrepignore, got clean exit\nstdout=%s\nstderr=%s", stdout, stderr)
	}
	if !strings.Contains(stderr, ".semgrepignore") {
		t.Fatalf("stderr should mention .semgrepignore:\n%s", stderr)
	}
	if !strings.Contains(stderr, "not supported") && !strings.Contains(stderr, "not allowed") && !strings.Contains(stderr, "disable") {
		t.Fatalf("stderr should explain why .semgrepignore is rejected:\n%s", stderr)
	}
}

func TestPathsRewrittenToRepoRelative(t *testing.T) {
	requireOpengrepOnPath(t)
	bin := buildOpengrepWrapper(t)
	repo := makeRepo(t)

	if err := os.MkdirAll(filepath.Join(repo, "pkg"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(repo, "pkg", "bad.go"), []byte(badGo), 0o644); err != nil {
		t.Fatal(err)
	}

	stdout, stderr, runErr := runWrapper(t, bin, repo, "pkg/bad.go")
	if runErr == nil {
		t.Fatalf("expected non-zero exit for erc006 finding; got clean\nstdout=%s\nstderr=%s", stdout, stderr)
	}
	if strings.Contains(stdout, repo) {
		t.Fatalf("absolute path leaked into stdout:\n%s", stdout)
	}
	if !strings.Contains(stripWhitespace(stdout), "pkg/bad.go") {
		t.Fatalf("expected repo-relative path pkg/bad.go in stdout:\n%s", stdout)
	}
}

// stripWhitespace removes all whitespace from s. Opengrep's text renderer
// wraps long finding paths onto a second line at an arbitrary column; the
// visual break is irrelevant to whether the wrapper produced a
// repo-relative path.
func stripWhitespace(s string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsSpace(r) {
			return -1
		}
		return r
	}, s)
}

func TestVersionFlag(t *testing.T) {
	bin := buildOpengrepWrapper(t)
	out, err := exec.Command(bin, "--version").Output()
	if err != nil {
		t.Fatalf("run --version: %v", err)
	}
	if string(out) != "erclint-opengrep dev\n" {
		t.Fatalf("--version stdout mismatch: %q", out)
	}
}

func buildOpengrepWrapper(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	bin := filepath.Join(dir, "erclint-opengrep")
	cmd := exec.Command("go", "build", "-o", bin, ".")
	cmd.Stdout = os.Stderr
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("build erclint-opengrep: %v", err)
	}
	return bin
}

func requireOpengrepOnPath(t *testing.T) {
	t.Helper()
	if _, err := exec.LookPath("opengrep"); err != nil {
		t.Fatalf("opengrep must be on PATH for wrapper tests (install: https://github.com/opengrep/opengrep/releases): %v", err)
	}
}

// makeRepo returns a symlink-resolved temp directory. Opengrep echoes back the
// canonical abs form of paths it receives; the tests compare against the same
// canonical form to keep the rewrite assertion stable on macOS's /tmp symlink.
func makeRepo(t *testing.T) string {
	t.Helper()
	raw := t.TempDir()
	resolved, err := filepath.EvalSymlinks(raw)
	if err != nil {
		t.Fatal(err)
	}
	return resolved
}

func runWrapper(t *testing.T, bin, cwd string, args ...string) (string, string, error) {
	t.Helper()
	cmd := exec.Command(bin, args...)
	cmd.Dir = cwd
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	return stdout.String(), stderr.String(), err
}

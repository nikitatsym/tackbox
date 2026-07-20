package main_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// --paths-from expands to the scan targets in place of positional files; the
// list-file plumbing itself must never become a scan target.
func TestPathsFromExpandsToScanTargets(t *testing.T) {
	bin, repo := setupPkgBadGoRepo(t)

	list := filepath.Join(repo, "list.txt")
	if err := os.WriteFile(list, []byte("pkg/bad.go\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	stdout, stderr, runErr := runWrapper(t, bin, repo, "--paths-from", list)
	if runErr == nil {
		t.Fatalf("expected finding via --paths-from; got clean\nstdout=%s\nstderr=%s", stdout, stderr)
	}
	if !strings.Contains(stdout, "go-exit-in-recover") {
		t.Fatalf("expected go-exit-in-recover via --paths-from:\n%s\nstderr=%s", stdout, stderr)
	}
	if !strings.Contains(stripWhitespace(stdout), "pkg/bad.go") {
		t.Fatalf("expected repo-relative pkg/bad.go via --paths-from:\n%s", stdout)
	}
	if strings.Contains(stdout, "list.txt") || strings.Contains(stdout, "--paths-from") {
		t.Fatalf("list-file plumbing leaked into scan targets:\n%s", stdout)
	}
}

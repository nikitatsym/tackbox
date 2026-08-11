package main

import (
	"encoding/json"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/nikitatsym/tackbox/go/internal/wrapcli"
)

// opengrepJSON is one opengrep --json result carrying the absolute path opengrep
// echoes back for a scan target.
func opengrepJSON(t *testing.T, path string) []byte {
	t.Helper()
	raw, err := json.Marshal(map[string]any{
		"results": []any{map[string]any{
			"check_id": "erclint.go-exit-in-recover",
			"path":     path,
			"start":    map[string]any{"line": 7},
			"extra":    map[string]any{"message": "m"},
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestRepoRelPathStripsCwdAndKeepsPosixSeparators(t *testing.T) {
	cwd := t.TempDir()
	if got := repoRelPath(filepath.Join(cwd, "pkg", "sub", "bad.go"), cwd); got != "pkg/sub/bad.go" {
		t.Fatalf("in-repo repoRelPath = %q, want pkg/sub/bad.go", got)
	}
	// Outside cwd nothing is stripped; the spelling is still POSIX.
	outside := filepath.Join(filepath.Dir(cwd), "other", "x.go")
	if got := repoRelPath(outside, cwd); got != filepath.ToSlash(outside) {
		t.Fatalf("out-of-repo repoRelPath = %q, want %q", got, filepath.ToSlash(outside))
	}
}

// A backslash is a legal file name character off Windows, so the POSIX rewrite
// must go through filepath.ToSlash and never a blanket string replace.
func TestRepoRelPathKeepsLiteralBackslashOffWindows(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("backslash is a path separator on windows")
	}
	cwd := t.TempDir()
	name := "od\\d.go"
	if got := repoRelPath(filepath.Join(cwd, name), cwd); got != name {
		t.Fatalf("repoRelPath = %q, want %q", got, name)
	}
}

// The machine contract is repo-relative POSIX: the hook compares the File field
// against an as_posix() path, so a Windows `\` misfiles the finding silently.
func TestMachineFindingFileIsRepoRelativePosix(t *testing.T) {
	cwd := t.TempDir()
	var buf strings.Builder
	if err := emitMachine(&buf, opengrepJSON(t, filepath.Join(cwd, "pkg", "bad.go")), cwd); err != nil {
		t.Fatal(err)
	}
	var f wrapcli.Finding
	if err := json.Unmarshal([]byte(strings.TrimSpace(buf.String())), &f); err != nil {
		t.Fatalf("machine output not NDJSON: %v (%q)", err, buf.String())
	}
	if f.File != "pkg/bad.go" || f.Line != 7 {
		t.Fatalf("machine finding = %+v, want pkg/bad.go:7", f)
	}
}

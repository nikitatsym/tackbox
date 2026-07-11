// Package gomodtest writes throwaway single-file Go modules for analyzer
// tests. Shared by the astutil and reporters test suites, which both need a
// tempdir holding a go.mod plus one source file.
package gomodtest

import (
	"os"
	"path/filepath"
)

// Write materializes a module in dir: go.mod (module fixture) plus rep.go
// holding source. Returns the source file's path. Callers own the tempdir
// (t.TempDir) and turn a returned error into t.Fatal, so this stays a plain
// helper with no testing dependency.
func Write(dir, source string) (string, error) {
	if err := os.WriteFile(filepath.Join(dir, "go.mod"), []byte("module fixture\n\ngo 1.21\n"), 0o644); err != nil {
		return "", err
	}
	file := filepath.Join(dir, "rep.go")
	if err := os.WriteFile(file, []byte(source), 0o644); err != nil {
		return "", err
	}
	return file, nil
}

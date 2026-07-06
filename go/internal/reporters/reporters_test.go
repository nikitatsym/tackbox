package reporters

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// writeFixture creates a throwaway module at t.TempDir()/mod with the given
// go.mod and source, returning the source file's absolute path.
func writeFixture(t *testing.T, source string) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "go.mod"), []byte("module fixture\n\ngo 1.21\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(dir, "rep.go")
	if err := os.WriteFile(file, []byte(source), 0o644); err != nil {
		t.Fatal(err)
	}
	return file
}

// unexported top-level function called only from package main's own main():
// not reachable from any exported/inlinable path outside the package, so
// export-data loading exposes an empty scope for it (verified separately).
const mainSource = `package main

import "errors"

func myReport(err error) {}

func main() {
	err := errors.New("x")
	if err != nil {
		myReport(err)
	}
}
`

// unexported top-level function with no caller at all in the package.
const uncalledSource = `package fixture

func myReport(err error) {}
`

func TestResolveUnexportedFunctionInMain(t *testing.T) {
	file := writeFixture(t, mainSource)
	decls, err := Resolve(file + "#myReport")
	if err != nil {
		t.Fatalf("Resolve of unexported top-level function must not hard-error: %v", err)
	}
	if len(decls) != 1 || decls[0].Name != "myReport" {
		t.Fatalf("want one declared reporter named myReport, got %+v", decls)
	}
}

func TestResolveUnexportedFunctionUncalled(t *testing.T) {
	file := writeFixture(t, uncalledSource)
	decls, err := Resolve(file + "#myReport")
	if err != nil {
		t.Fatalf("Resolve of unexported top-level function must not hard-error: %v", err)
	}
	if len(decls) != 1 || decls[0].Name != "myReport" {
		t.Fatalf("want one declared reporter named myReport, got %+v", decls)
	}
}

func TestResolveDeadSymbolStillErrors(t *testing.T) {
	file := writeFixture(t, mainSource)
	_, err := Resolve(file + "#nope")
	if err == nil {
		t.Fatal("Resolve of a genuinely nonexistent function must hard-error")
	}
	if !strings.Contains(err.Error(), "no top-level function nope") {
		t.Fatalf("unexpected error: %v", err)
	}
}

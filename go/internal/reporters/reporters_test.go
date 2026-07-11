package reporters

import (
	"strings"
	"testing"

	"github.com/nikitatsym/tackbox/go/internal/gomodtest"
)

// writeFixture creates a throwaway module at t.TempDir() with the given
// source, returning the source file's absolute path.
func writeFixture(t *testing.T, source string) string {
	t.Helper()
	file, err := gomodtest.Write(t.TempDir(), source)
	if err != nil {
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

// assertResolvesMyReport: Resolve must return exactly the one myReport decl
// without hard-erroring, whatever the caller shape in source.
func assertResolvesMyReport(t *testing.T, source string) {
	t.Helper()
	file := writeFixture(t, source)
	decls, err := Resolve(file + "#myReport")
	if err != nil {
		t.Fatalf("Resolve of unexported top-level function must not hard-error: %v", err)
	}
	if len(decls) != 1 || decls[0].Name != "myReport" {
		t.Fatalf("want one declared reporter named myReport, got %+v", decls)
	}
}

func TestResolveUnexportedFunctionInMain(t *testing.T) {
	assertResolvesMyReport(t, mainSource)
}

func TestResolveUnexportedFunctionUncalled(t *testing.T) {
	assertResolvesMyReport(t, uncalledSource)
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

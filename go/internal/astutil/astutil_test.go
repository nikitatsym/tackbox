package astutil_test

import (
	"go/ast"
	"go/types"
	"os"
	"path/filepath"
	"testing"

	"golang.org/x/tools/go/packages"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

// source declares an unexported top-level function and a call site that
// hands the guarded err into it - the shape a `.tackbox-reporters`
// declaration names.
const source = `package fixture

import "errors"

func myReport(err error) {}

func Handler() error {
	err := errors.New("x")
	if err != nil {
		myReport(err)
	}
	return errors.New("noop")
}
`

// loadCall parses source into a full types.Info and returns the `myReport(err)`
// call expression plus the info that resolved it.
func loadCall(t *testing.T) (*ast.CallExpr, *types.Info) {
	t.Helper()
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "go.mod"), []byte("module fixture\n\ngo 1.21\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "rep.go"), []byte(source), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg := &packages.Config{
		Mode: packages.NeedName | packages.NeedFiles | packages.NeedSyntax | packages.NeedTypes | packages.NeedTypesInfo,
		Dir:  dir,
	}
	pkgs, err := packages.Load(cfg, "./...")
	if err != nil {
		t.Fatal(err)
	}
	if len(pkgs) != 1 || len(pkgs[0].Syntax) != 1 {
		t.Fatalf("want one package with one file, got %+v", pkgs)
	}
	pkg := pkgs[0]
	var call *ast.CallExpr
	ast.Inspect(pkg.Syntax[0], func(n ast.Node) bool {
		if c, ok := n.(*ast.CallExpr); ok {
			if id, ok := c.Fun.(*ast.Ident); ok && id.Name == "myReport" {
				call = c
			}
		}
		return true
	})
	if call == nil {
		t.Fatal("myReport call not found")
	}
	return call, pkg.TypesInfo
}

// TestCaptureKindMatchesUnexportedDeclaredReporter proves the MATCH side of
// the declared-reporter contract: astutil resolves a call's callee via real
// type information (not name-only matching), so an unexported top-level
// function declared via SetDeclaredReporters is recognized as a capture.
func TestCaptureKindMatchesUnexportedDeclaredReporter(t *testing.T) {
	call, info := loadCall(t)

	astutil.SetDeclaredReporters(nil)
	if astutil.IsCaptureErr(info, call, "err") {
		t.Fatal("undeclared myReport must not be recognized as a capture")
	}

	astutil.SetDeclaredReporters([]astutil.DeclaredReporter{{PkgPath: "fixture", Name: "myReport"}})
	defer astutil.SetDeclaredReporters(nil)
	if !astutil.IsCaptureErr(info, call, "err") {
		t.Fatal("declared unexported myReport must resolve and match as a capture")
	}

	astutil.SetDeclaredReporters([]astutil.DeclaredReporter{{PkgPath: "fixture", Name: "otherName"}})
	if astutil.IsCaptureErr(info, call, "err") {
		t.Fatal("a declaration for a different name must not match myReport's call site")
	}
}

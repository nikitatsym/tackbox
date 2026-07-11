// Package skiptest enforces ERC008: a skipped test must say why.
// In *_test.go files a Skip/Skipf on testing.T/B/F needs a non-empty
// reason argument, and a bare SkipNow needs a `// test-skip: <reason>`
// marker in the comment block directly above. This is the only
// analyzer whose subject is the tests themselves.
package skiptest

import (
	"go/ast"
	"go/token"
	"strconv"
	"strings"

	"golang.org/x/tools/go/analysis"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
	"github.com/nikitatsym/tackbox/go/internal/markers"
)

var Analyzer = &analysis.Analyzer{
	Name: "skiptest",
	Doc:  "ERC008: a skipped test must carry a non-empty reason (Skip/Skipf argument or `// test-skip: <reason>`)",
	Run:  run,
}

var skipMethods = map[string]bool{"Skip": true, "Skipf": true, "SkipNow": true}

func run(pass *analysis.Pass) (interface{}, error) {
	astutil.EachTestFile(pass, func(f *ast.File) {
		idx := markers.Build(pass.Fset, f)
		ast.Inspect(f, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			sel, ok := call.Fun.(*ast.SelectorExpr)
			if !ok || !skipMethods[sel.Sel.Name] {
				return true
			}
			// Origin, not name: the method must resolve to package
			// testing (covers T, B, F and the TB interface; a local
			// type's own Skip is not a test skip).
			obj := pass.TypesInfo.ObjectOf(sel.Sel)
			if obj == nil || obj.Pkg() == nil || obj.Pkg().Path() != "testing" {
				return true
			}
			if hasReason(call, sel.Sel.Name) {
				return true
			}
			if m, ok := idx.Above(call); ok && m.Kind == markers.TestSkip {
				return true
			}
			pass.Reportf(call.Pos(),
				"ERC008: skipped test must state a reason: pass it to %s",
				sel.Sel.Name)
			return true
		})
	})
	return nil, nil
}

// hasReason reports whether the skip call itself carries a non-empty
// reason. Non-literal arguments are trusted; only a missing argument
// list or all-empty string literals fail.
func hasReason(call *ast.CallExpr, method string) bool {
	if method == "SkipNow" {
		return false
	}
	if len(call.Args) == 0 {
		return false
	}
	for _, a := range call.Args {
		lit, ok := a.(*ast.BasicLit)
		if !ok {
			return true
		}
		if lit.Kind != token.STRING {
			return true
		}
		if s, err := strconv.Unquote(lit.Value); err == nil && strings.TrimSpace(s) != "" {
			return true
		}
	}
	return false
}

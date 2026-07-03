// Package terminal implements ERC003: every terminal exit
// (`log.Fatal*`, `os.Exit`, project-local `die`) must be preceded
// by a capture call in the same block or carry an explicit
// `// no-sentry: <reason>` marker directly above the call.
package terminal

import (
	"go/ast"
	"go/types"
	"strings"

	"golang.org/x/tools/go/analysis"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
	"github.com/nikitatsym/tackbox/go/internal/markers"
)

var Analyzer = &analysis.Analyzer{
	Name: "terminal",
	Doc:  "ERC003: terminal exit must be preceded by capture or carry `// no-sentry:` marker",
	Run:  run,
}

func run(pass *analysis.Pass) (interface{}, error) {
	astutil.EachFile(pass, func(f *ast.File) {
		idx := markers.Build(pass.Fset, f)
		ast.Inspect(f, func(n ast.Node) bool {
			block, ok := n.(*ast.BlockStmt)
			if !ok {
				return true
			}
			for i, st := range block.List {
				call, ok := terminalCall(st)
				if !ok {
					continue
				}
				if m, ok := idx.Above(st); ok && m.Kind == markers.NoSentry {
					continue
				}
				if hasCaptureBefore(pass.TypesInfo, block.List[:i]) {
					continue
				}
				pass.Reportf(call.Pos(),
					"ERC003: terminal exit `%s` must be preceded by a capture (go/report or declared sink) or carry `// no-sentry: <reason>`",
					astutil.QualifiedName(call.Fun))
			}
			return true
		})
	})
	return nil, nil
}

func terminalCall(st ast.Stmt) (*ast.CallExpr, bool) {
	exprSt, ok := st.(*ast.ExprStmt)
	if !ok {
		return nil, false
	}
	call, ok := exprSt.X.(*ast.CallExpr)
	if !ok {
		return nil, false
	}
	if name := astutil.QualifiedName(call.Fun); name == "os.Exit" || strings.HasPrefix(name, "log.Fatal") {
		return call, true
	}
	if id, ok := call.Fun.(*ast.Ident); ok && id.Name == "die" {
		return call, true
	}
	return nil, false
}

func hasCaptureBefore(info *types.Info, stmts []ast.Stmt) bool {
	for _, st := range stmts {
		call, ok := callFromStmt(st)
		if ok && astutil.IsCapture(info, call, "") {
			return true
		}
	}
	return false
}

func callFromStmt(st ast.Stmt) (*ast.CallExpr, bool) {
	switch s := st.(type) {
	case *ast.ExprStmt:
		if c, ok := s.X.(*ast.CallExpr); ok {
			return c, true
		}
	case *ast.AssignStmt:
		if len(s.Rhs) == 1 {
			if c, ok := s.Rhs[0].(*ast.CallExpr); ok {
				return c, true
			}
		}
	}
	return nil, false
}

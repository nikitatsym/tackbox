// Package terminal implements ERC003: every terminal exit (`log.Fatal*`,
// `os.Exit`, project-local `die`) must either be preceded by a capture in the
// same block, carry the error into its own arguments (`log.Fatal(err)` - a
// reported death), or carry a `// no-sentry: <reason>` marker directly above
// the call. A silent exit (`os.Exit(1)` in an err-branch, `log.Fatal("msg")`
// with the live error dropped) stays a finding.
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
	Doc:  "ERC003: terminal exit must capture, carry the error, or carry `// no-sentry:` marker",
	Run:  run,
}

func run(pass *analysis.Pass) (interface{}, error) {
	astutil.EachFile(pass, func(f *ast.File) {
		idx := markers.Build(pass.Fset, f)
		branchErr := enclosingErrNames(f)
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
				if carriesErr(call, branchErr[call]) {
					continue
				}
				pass.Reportf(call.Pos(),
					"ERC003: terminal exit `%s` must be preceded by a capture, carry the error into its arguments, or carry `// no-sentry: <reason>`",
					astutil.QualifiedName(call.Fun))
			}
			return true
		})
	})
	return nil, nil
}

// enclosingErrNames maps each terminal call to the error identifiers of the
// `if <e> != nil` branches lexically enclosing it (nested closures excluded).
// A terminal carrying any such error in its own arguments is a reported death.
func enclosingErrNames(f *ast.File) map[*ast.CallExpr][]string {
	out := map[*ast.CallExpr][]string{}
	ast.Inspect(f, func(n ast.Node) bool {
		ifst, ok := n.(*ast.IfStmt)
		if !ok {
			return true
		}
		e := astutil.ErrIdentFromIfCond(ifst.Cond)
		if e == "" {
			return true
		}
		for _, call := range terminalCallsIn(ifst.Body) {
			out[call] = append(out[call], e)
		}
		return true
	})
	return out
}

// carriesErr reports whether one of the enclosing err-branch errors flows into
// the terminal call's arguments. A declared die-helper (`die(err)`) is covered
// here too: `die` is the only in-repo-declarable terminal name and the error
// reaches its arguments, so option (c) collapses into this argument-flow check.
func carriesErr(call *ast.CallExpr, errNames []string) bool {
	for _, e := range errNames {
		if astutil.ArgFlows(call, e) {
			return true
		}
	}
	return false
}

func terminalCallsIn(body *ast.BlockStmt) []*ast.CallExpr {
	var out []*ast.CallExpr
	ast.Inspect(body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		if st, ok := n.(*ast.ExprStmt); ok {
			if call, ok := terminalCall(st); ok {
				out = append(out, call)
			}
		}
		return true
	})
	return out
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

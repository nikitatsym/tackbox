// Package terminal implements ERC003: every terminal exit (`log.Fatal*`,
// `os.Exit`, project-local `die`) must either be preceded by a capture in the
// same block, carry the error into its own arguments (`log.Fatal(err)` - a
// reported death), or carry a no-report marker with a reason directly above
// the call. A silent exit (`os.Exit(1)` in an err-branch, `log.Fatal("msg")`
// with the live error dropped) stays a finding.
//
// A declared usage sink (`[usage]` in `.tackbox/reporters`) is the opposite,
// single-purpose lane: a deliberate diagnostic exit - clean outside
// err-branches, a finding inside one regardless of arguments. Raw exits and
// undeclared wrappers keep the strict contract above.
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
	Doc:  "ERC003: terminal exit must capture, carry the error, or carry `// no-report:` marker",
	Run:  run,
}

func run(pass *analysis.Pass) (interface{}, error) {
	astutil.InspectNonDeclared(pass, func(f *ast.File) func(ast.Node) bool {
		idx := markers.Build(pass.Fset, f)
		branchErr := enclosingErrNames(pass.TypesInfo, f)
		return func(n ast.Node) bool {
			block, ok := n.(*ast.BlockStmt)
			if !ok {
				return true
			}
			for i, st := range block.List {
				call, class := classify(pass.TypesInfo, st)
				if class == callNone {
					continue
				}
				if m, ok := idx.Above(st); ok && m.Kind == markers.NoReport {
					continue
				}
				if class == callUsage {
					if len(branchErr[call]) > 0 {
						pass.Reportf(call.Pos(),
							"ERC003: usage sink `%s` on a failure path - capture and exit, or log.Fatal(err)",
							astutil.QualifiedName(call.Fun))
					}
					continue
				}
				if hasCaptureBefore(pass.TypesInfo, block.List[:i]) {
					continue
				}
				if carriesErr(call, branchErr[call]) {
					continue
				}
				pass.Reportf(call.Pos(),
					"ERC003: terminal exit `%s` must be preceded by a capture or carry the error into its arguments",
					astutil.QualifiedName(call.Fun))
			}
			return true
		}
	})
	return nil, nil
}

// enclosingErrNames maps each terminal or usage-sink call to the error
// identifiers of the `if <e> != nil` branches lexically enclosing it (nested
// closures excluded). A terminal carrying any such error in its own arguments
// is a reported death; a usage sink with any entry is on a failure path.
func enclosingErrNames(info *types.Info, f *ast.File) map[*ast.CallExpr][]string {
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
		for _, call := range terminalCallsIn(info, ifst.Body) {
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

func terminalCallsIn(info *types.Info, body *ast.BlockStmt) []*ast.CallExpr {
	var out []*ast.CallExpr
	ast.Inspect(body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		if st, ok := n.(*ast.ExprStmt); ok {
			if call, class := classify(info, st); class != callNone {
				out = append(out, call)
			}
		}
		return true
	})
	return out
}

type callClass int

const (
	callNone callClass = iota
	callTerminal
	callUsage
)

// classify sorts a statement into the two exit lanes. Usage wins over the
// terminal names: a declared usage sink named `die` gets usage semantics.
func classify(info *types.Info, st ast.Stmt) (*ast.CallExpr, callClass) {
	exprSt, ok := st.(*ast.ExprStmt)
	if !ok {
		return nil, callNone
	}
	call, ok := exprSt.X.(*ast.CallExpr)
	if !ok {
		return nil, callNone
	}
	if astutil.IsUsageSink(info, call) {
		return call, callUsage
	}
	if name := astutil.QualifiedName(call.Fun); name == "os.Exit" || strings.HasPrefix(name, "log.Fatal") {
		return call, callTerminal
	}
	if id, ok := call.Fun.(*ast.Ident); ok && id.Name == "die" {
		return call, callTerminal
	}
	return nil, callNone
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

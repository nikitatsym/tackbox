// Package recoverswallow implements ERC007: a recovered panic must be
// reported - the recovered value flows into a go/report capture or a
// `.tackbox-reporters` sink - or re-panicked so the original stack survives.
// A bare recover that swallows the value is a finding unless it carries a
// `// no-report: <reason>` marker.
package recoverswallow

import (
	"go/ast"
	"go/types"

	"golang.org/x/tools/go/analysis"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
	"github.com/nikitatsym/tackbox/go/internal/markers"
)

var Analyzer = &analysis.Analyzer{
	Name: "recoverswallow",
	Doc:  "ERC007: recovered panic must be reported or re-panicked, or carry `// no-report:` marker",
	Run:  markers.Runner(inspect),
}

const msg = "ERC007: recovered panic must be reported (go/report or declared sink receiving it) or re-panicked"

func inspect(idx *markers.Index, pass *analysis.Pass, n ast.Node) bool {
	switch x := n.(type) {
	case *ast.IfStmt:
		handleIf(pass, idx, x)
	case *ast.BlockStmt:
		handleBlock(pass, idx, x)
	}
	return true
}

// handleIf covers the canonical guard `if r := recover(); r != nil { ... }`.
func handleIf(pass *analysis.Pass, idx *markers.Index, ifst *ast.IfStmt) {
	assign, ok := ifst.Init.(*ast.AssignStmt)
	if !ok {
		return
	}
	name, call, ok := recoverAssign(pass.TypesInfo, assign)
	if !ok || astutil.ErrIdentFromIfCond(ifst.Cond) != name {
		return
	}
	if markerAbove(idx, ifst) {
		return
	}
	if reported(pass.TypesInfo, astutil.BlockCalls(ifst.Body), name) {
		return
	}
	pass.Reportf(call.Pos(), msg)
}

// handleBlock covers a bare `recover()` (value discarded) and the assignment
// form `r := recover()` answered by the statements that follow it. The if-init
// form is owned by handleIf and never lands in a block's statement list.
func handleBlock(pass *analysis.Pass, idx *markers.Index, block *ast.BlockStmt) {
	for i, st := range block.List {
		switch s := st.(type) {
		case *ast.ExprStmt:
			if call, ok := recoverCall(pass.TypesInfo, s.X); ok && !markerAbove(idx, s) {
				pass.Reportf(call.Pos(), msg)
			}
		case *ast.AssignStmt:
			name, call, ok := recoverAssign(pass.TypesInfo, s)
			if !ok || markerAbove(idx, s) {
				continue
			}
			if name != "_" && reported(pass.TypesInfo, callsIn(block.List[i+1:]), name) {
				continue
			}
			pass.Reportf(call.Pos(), msg)
		}
	}
}

func recoverAssign(info *types.Info, assign *ast.AssignStmt) (string, *ast.CallExpr, bool) {
	if len(assign.Rhs) != 1 || len(assign.Lhs) != 1 {
		return "", nil, false
	}
	call, ok := recoverCall(info, assign.Rhs[0])
	if !ok {
		return "", nil, false
	}
	id, ok := assign.Lhs[0].(*ast.Ident)
	if !ok {
		return "", nil, false
	}
	return id.Name, call, true
}

func recoverCall(info *types.Info, e ast.Expr) (*ast.CallExpr, bool) {
	call, ok := e.(*ast.CallExpr)
	if !ok {
		return nil, false
	}
	id, ok := call.Fun.(*ast.Ident)
	if !ok || id.Name != "recover" {
		return nil, false
	}
	if _, ok := info.Uses[id].(*types.Builtin); !ok {
		return nil, false
	}
	return call, true
}

// reported reports whether the recovered value `name` reaches a recognized
// reporter call or a re-panic. go/report captures are package-gated but must
// still receive the value; declared sinks already require argument-flow.
func reported(info *types.Info, calls []*ast.CallExpr, name string) bool {
	for _, call := range calls {
		if id, ok := call.Fun.(*ast.Ident); ok && id.Name == "panic" && astutil.ArgFlows(call, name) {
			return true
		}
		if astutil.IsCapture(info, call, name) && astutil.ArgFlows(call, name) {
			return true
		}
	}
	return false
}

func callsIn(stmts []ast.Stmt) []*ast.CallExpr {
	var out []*ast.CallExpr
	for _, st := range stmts {
		ast.Inspect(st, func(n ast.Node) bool {
			if _, ok := n.(*ast.FuncLit); ok {
				return false
			}
			if c, ok := n.(*ast.CallExpr); ok {
				out = append(out, c)
			}
			return true
		})
	}
	return out
}

func markerAbove(idx *markers.Index, node ast.Node) bool {
	m, ok := idx.Above(node)
	return ok && m.Kind == markers.NoReport
}

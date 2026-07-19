// Package notifygate implements ERC009: a go/report.Notify that terminates a
// failure path must be narrowed. Go has no typed catch, so the narrowing gate
// is structural - the notify must sit under an ADDITIONAL condition (an
// if / switch / select branch inside the err-branch), not stand as the
// unconditional sole handling of the whole `if err != nil` branch. An
// unconditional notify would route every failure to a toast and blind the
// telemetry the operator watches (D006); gate strength is proportional to
// observability loss, and notify drops the only channel the operator sees.
//
// Only a notify carrying the caught error (argument-flow) is gated - a notify
// the error does not reach is not terminating this failure path. The complement
// of a narrowed notify stays covered by ERC001. A no-report marker above
// the branch suppresses; a new marker needs user approval.
package notifygate

import (
	"go/ast"

	"golang.org/x/tools/go/analysis"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
	"github.com/nikitatsym/tackbox/go/internal/markers"
)

var Analyzer = &analysis.Analyzer{
	Name: "notifygate",
	Doc:  "ERC009: a notify terminating an err-branch must sit under an additional condition, not handle the whole branch unconditionally",
	Run:  markers.Runner(inspect),
}

func inspect(idx *markers.Index, pass *analysis.Pass, n ast.Node) bool {
	ifst, errIdent, ok := astutil.ErrBranch(pass.TypesInfo, n)
	if !ok {
		return true
	}
	if m, ok := idx.Above(ifst); ok && m.Kind == markers.NoReport {
		return true
	}
	names := astutil.ErrAliases(ifst.Body, errIdent.Name)
	parent := parentMap(ifst.Body)
	ast.Inspect(ifst.Body, func(x ast.Node) bool {
		if _, ok := x.(*ast.FuncLit); ok {
			return false
		}
		call, ok := x.(*ast.CallExpr)
		if !ok || !astutil.IsReportNotify(pass.TypesInfo, call) || !argFlowsAny(call, names) {
			return true
		}
		if guarded(parent, ifst.Body, call) {
			return true
		}
		pass.Reportf(call.Pos(),
			"ERC009: notify handles this err-branch unconditionally - it routes every failure to the user "+
				"lane and blinds telemetry; put it under a condition and report the complement with "+
				"report.Error/report.Warn, or capture instead; a new `// no-report:` marker needs user approval "+
				"(err=%s)", errIdent.Name)
		return true
	})
	return true
}

func argFlowsAny(call *ast.CallExpr, names []string) bool {
	for _, name := range names {
		if astutil.ArgFlows(call, name) {
			return true
		}
	}
	return false
}

// guarded reports whether call sits under an additional condition strictly
// inside body: an if-branch (the then or else leg) or a switch/select case. A
// call reachable from body without crossing such a construct is unconditional.
func guarded(parent map[ast.Node]ast.Node, body *ast.BlockStmt, call ast.Node) bool {
	for cur := call; cur != nil && cur != ast.Node(body); {
		p := parent[cur]
		switch pp := p.(type) {
		case *ast.IfStmt:
			if cur == ast.Node(pp.Body) || cur == pp.Else {
				return true
			}
		case *ast.CaseClause, *ast.CommClause:
			return true
		}
		cur = p
	}
	return false
}

// parentMap records each node's parent within root, so guarded can walk from a
// notify call up to the err-branch body. A stack tracks the current ancestry:
// ast.Inspect pushes on each node and pops on the trailing nil call.
func parentMap(root ast.Node) map[ast.Node]ast.Node {
	parent := map[ast.Node]ast.Node{}
	var stack []ast.Node
	ast.Inspect(root, func(n ast.Node) bool {
		if n == nil {
			if len(stack) > 0 {
				stack = stack[:len(stack)-1]
			}
			return true
		}
		if len(stack) > 0 {
			parent[n] = stack[len(stack)-1]
		}
		stack = append(stack, n)
		return true
	})
	return parent
}

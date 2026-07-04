// Package errcheck implements ERC001: every `err != nil` branch must
// propagate the error, capture it (a go/report call or a `.tackbox-reporters`
// sink), report it via a printing terminal exit (`log.Fatal*`/`die` carrying
// the error - a reported death; `os.Exit` prints nothing and is excluded), or
// carry a `// no-report: <reason>` marker on the line directly above the if.
package errcheck

import (
	"go/ast"
	"go/types"
	"strings"

	"golang.org/x/tools/go/analysis"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
	"github.com/nikitatsym/tackbox/go/internal/markers"
)

var Analyzer = &analysis.Analyzer{
	Name: "errcheck",
	Doc:  "ERC001: err-branches must propagate, capture, report via terminal exit, or carry `// no-report:` marker",
	Run:  run,
}

func run(pass *analysis.Pass) (interface{}, error) {
	astutil.EachFile(pass, func(f *ast.File) {
		idx := markers.Build(pass.Fset, f)
		ast.Inspect(f, func(n ast.Node) bool {
			ifst, ok := n.(*ast.IfStmt)
			if !ok {
				return true
			}
			errName := astutil.ErrIdentFromIfCond(ifst.Cond)
			if errName == "" {
				return true
			}
			if m, ok := idx.Above(ifst); ok && m.Kind == markers.NoReport {
				return true
			}
			if propagates(ifst.Body, errName) {
				return true
			}
			if captures(pass.TypesInfo, ifst.Body, errName) {
				return true
			}
			if reportsDeath(ifst.Body, errName) {
				return true
			}
			pass.Reportf(ifst.Pos(),
				"ERC001: err-branch must propagate, capture, carry the error into a terminal exit, or carry `// no-report: <reason>` (err=%s)",
				errName)
			return true
		})
	})
	return nil, nil
}

func propagates(body *ast.BlockStmt, errName string) bool {
	for _, ret := range astutil.BlockReturns(body) {
		for _, res := range ret.Results {
			if astutil.ContainsIdent(res, errName) {
				return true
			}
		}
	}
	for _, call := range astutil.BlockCalls(body) {
		id, ok := call.Fun.(*ast.Ident)
		if !ok || id.Name != "panic" {
			continue
		}
		for _, arg := range call.Args {
			if astutil.ContainsIdent(arg, errName) {
				return true
			}
		}
	}
	return false
}

func captures(info *types.Info, body *ast.BlockStmt, errName string) bool {
	for _, call := range astutil.BlockCalls(body) {
		if astutil.IsCapture(info, call, errName) {
			return true
		}
	}
	return false
}

// reportsDeath reports whether the err-branch ends in a reported death: a
// printing terminal (`log.Fatal*`/`die`) carrying the checked error into its
// arguments - the same argument-flow ERC003 uses. The error reaches a call that
// prints it and never returns, so the branch is handled.
func reportsDeath(body *ast.BlockStmt, errName string) bool {
	for _, call := range astutil.BlockCalls(body) {
		if isPrintingTerminal(call) && astutil.ArgFlows(call, errName) {
			return true
		}
	}
	return false
}

// isPrintingTerminal reports whether call is a terminal that prints its
// arguments (`log.Fatal*` or the in-repo `die`). os.Exit is excluded: it prints
// nothing, so carrying the error into it reports nothing, and name-based
// ArgFlows would otherwise accept `os.Exit(len(err.Error()))`.
func isPrintingTerminal(call *ast.CallExpr) bool {
	if strings.HasPrefix(astutil.QualifiedName(call.Fun), "log.Fatal") {
		return true
	}
	id, ok := call.Fun.(*ast.Ident)
	return ok && id.Name == "die"
}

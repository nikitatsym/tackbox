// Package errcheck implements ERC001: every `err != nil` branch must
// propagate the error, capture it (a go/report call or a `.tackbox-reporters`
// sink), or carry a `// no-sentry: <reason>` marker on the line directly
// above the if.
package errcheck

import (
	"go/ast"
	"go/types"

	"golang.org/x/tools/go/analysis"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
	"github.com/nikitatsym/tackbox/go/internal/markers"
)

var Analyzer = &analysis.Analyzer{
	Name: "errcheck",
	Doc:  "ERC001: err-branches must propagate, capture, or carry `// no-sentry:` marker",
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
			if m, ok := idx.Above(ifst); ok && m.Kind == markers.NoSentry {
				return true
			}
			if propagates(ifst.Body, errName) {
				return true
			}
			if captures(pass.TypesInfo, ifst.Body, errName) {
				return true
			}
			pass.Reportf(ifst.Pos(),
				"ERC001: err-branch must propagate, capture, or carry `// no-sentry: <reason>` (err=%s)",
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

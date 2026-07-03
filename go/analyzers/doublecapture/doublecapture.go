// Package doublecapture implements ERC005: a single err-branch must
// not both capture (a go/report SentryErr/Warn call or a declared sink)
// and `return err` — the upstream handler would re-capture and inflate
// Sentry counts. Pick one: capture and swallow, or propagate without
// capture. Panic-capture is terminal and excluded.
package doublecapture

import (
	"go/ast"
	"go/types"

	"golang.org/x/tools/go/analysis"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

var Analyzer = &analysis.Analyzer{
	Name: "doublecapture",
	Doc:  "ERC005: err-branch must not both capture and `return err`",
	Run:  run,
}

func run(pass *analysis.Pass) (interface{}, error) {
	astutil.EachFile(pass, func(f *ast.File) {
		ast.Inspect(f, func(n ast.Node) bool {
			ifst, ok := n.(*ast.IfStmt)
			if !ok {
				return true
			}
			errName := astutil.ErrIdentFromIfCond(ifst.Cond)
			if errName == "" {
				return true
			}
			if !hasCaptureNotPanic(pass.TypesInfo, ifst.Body, errName) {
				return true
			}
			if !hasReturnReferencingErr(ifst.Body, errName) {
				return true
			}
			pass.Reportf(ifst.Pos(),
				"ERC005: err-branch must not both capture and `return err` (err=%s)",
				errName)
			return true
		})
	})
	return nil, nil
}

func hasCaptureNotPanic(info *types.Info, body *ast.BlockStmt, errName string) bool {
	for _, call := range astutil.BlockCalls(body) {
		if astutil.IsCaptureErr(info, call, errName) {
			return true
		}
	}
	return false
}

func hasReturnReferencingErr(body *ast.BlockStmt, errName string) bool {
	for _, ret := range astutil.BlockReturns(body) {
		for _, res := range ret.Results {
			if astutil.ContainsIdent(res, errName) {
				return true
			}
		}
	}
	return false
}

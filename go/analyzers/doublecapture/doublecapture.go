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
	astutil.InspectNonDeclared(pass, func(_ *ast.File) func(ast.Node) bool {
		return func(n ast.Node) bool {
			ifst, ok := n.(*ast.IfStmt)
			if !ok {
				return true
			}
			errName := astutil.ErrIdentFromIfCond(ifst.Cond)
			if errName == "" {
				return true
			}
			// errors.As aliases hold the same error object on both legs.
			names := astutil.ErrAliases(ifst.Body, errName)
			captured, returned := false, false
			for _, name := range names {
				captured = captured || hasCaptureNotPanic(pass.TypesInfo, ifst.Body, name)
				returned = returned || hasReturnReferencingErr(pass.TypesInfo, ifst.Body, name)
			}
			if !captured || !returned {
				return true
			}
			pass.Reportf(ifst.Pos(),
				"ERC005: err-branch must not both capture and `return err` (err=%s)",
				errName)
			return true
		}
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

// A result that cannot hand an error to the caller (a sink's exit code) is
// not `return err`: `return DeclaredSink(err)` is a single capture.
func hasReturnReferencingErr(info *types.Info, body *ast.BlockStmt, errName string) bool {
	for _, ret := range astutil.BlockReturns(body) {
		for _, res := range ret.Results {
			if astutil.ContainsIdent(res, errName) && astutil.IsErrorCarryingExpr(info, res) {
				return true
			}
		}
	}
	return false
}

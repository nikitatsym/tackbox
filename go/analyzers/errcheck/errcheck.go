// Package errcheck implements ERC001: every `err != nil` branch must
// propagate the error, capture it (a go/report call or a `.tackbox-reporters`
// sink), route it to the user lane via a go/report.Notify carrying it (whether
// that notify is narrow enough is ERC009's call, not this one), report it via a
// printing terminal exit (`log.Fatal*`/`die` carrying the error - a reported
// death; `os.Exit` prints nothing and is excluded), or carry a
// `// no-report: <reason>` marker on the line directly above the if.
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
	Doc:  "ERC001: err-branches must propagate, capture, report via terminal exit, or carry `// no-report:` marker",
	Run:  markers.Runner(inspect),
}

func inspect(idx *markers.Index, pass *analysis.Pass, n ast.Node) bool {
	// Type-gate: only an `if <err> != nil` guard of an error-assignable
	// identifier is an err-branch. `if conn != nil` on a *net.Conn is not one.
	ifst, errIdent, ok := astutil.ErrBranch(pass.TypesInfo, n)
	if !ok {
		return true
	}
	errName := errIdent.Name
	if m, ok := idx.Above(ifst); ok && m.Kind == markers.NoReport {
		return true
	}
	// errors.As aliases hold the same error object: any exit
	// through an alias is an exit of the guarded error.
	for _, name := range astutil.ErrAliases(ifst.Body, errName) {
		if propagates(pass.TypesInfo, ifst.Body, name) ||
			captures(pass.TypesInfo, ifst.Body, name) ||
			notifies(pass.TypesInfo, ifst.Body, name) ||
			reportsDeath(pass.TypesInfo, ifst.Body, name) {
			return true
		}
	}
	pass.Reportf(ifst.Pos(),
		"ERC001: err-branch must propagate, capture, or carry the error into a terminal exit (err=%s)",
		errName)
	return true
}

// propagates reports whether the err-branch carries the checked error onward:
// a chain-preserving return (`return err` / `%w` wrap / errors.Join) or a
// `panic` carrying it. A `%v` / `.Error()` return breaks the unwrap chain and
// is not propagation (rethrow-without-cause).
func propagates(info *types.Info, body *ast.BlockStmt, errName string) bool {
	if astutil.BlockPropagatesChain(info, body, errName) {
		return true
	}
	for _, call := range astutil.BlockCalls(body) {
		id, ok := call.Fun.(*ast.Ident)
		if !ok || id.Name != "panic" {
			continue
		}
		if astutil.ArgFlows(call, errName) {
			return true
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

// notifies reports whether the err-branch routes the checked error to the user
// lane via a go/report.Notify carrying it - a terminal for that path (D006), so
// a notified branch does not read as a swallow. ERC009 decides whether the
// notify is narrow enough; notify is never a capture, so ERC005/ERC006 are
// unaffected.
func notifies(info *types.Info, body *ast.BlockStmt, errName string) bool {
	for _, call := range astutil.BlockCalls(body) {
		if astutil.IsReportNotify(info, call) && astutil.ArgFlows(call, errName) {
			return true
		}
	}
	return false
}

// reportsDeath reports whether the err-branch ends in a reported death: a
// printing terminal (`log.Fatal*`/`die`) carrying the checked error into its
// arguments - the same argument-flow ERC003 uses. The error reaches a call that
// prints it and never returns, so the branch is handled.
func reportsDeath(info *types.Info, body *ast.BlockStmt, errName string) bool {
	for _, call := range astutil.BlockCalls(body) {
		if astutil.IsPrintingTerminal(info, call) && astutil.ArgFlows(call, errName) {
			return true
		}
	}
	return false
}

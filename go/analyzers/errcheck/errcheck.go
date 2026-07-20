// Package errcheck implements ERC001: every execution path through an
// `err != nil` branch must propagate the error, capture it (a go/report call or
// a `.tackbox/reporters` sink), route it to the user lane via a go/report.Notify
// carrying it (whether that notify is narrow enough is ERC009's call, not this
// one), report it via a printing terminal exit (`log.Fatal*`/`die` carrying the
// error - a reported death; `os.Exit` prints nothing and is excluded), or the
// whole if must carry a no-report marker with a reason on the line directly
// above it. The walk is path-sensitive (flow.go): a handled if/else leg does
// not credit its silent complement.
package errcheck

import (
	"go/ast"

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
	// errors.As aliases hold the same error object: a handle through an alias
	// is a handle of the guarded error.
	aliases := astutil.ErrAliases(ifst.Body, errName)
	if hasSilentPath(pass.TypesInfo, ifst.Body, aliases) {
		pass.Reportf(ifst.Pos(),
			"ERC001: err-branch must propagate, capture, or carry the error into a terminal exit (err=%s)",
			errName)
	}
	return true
}

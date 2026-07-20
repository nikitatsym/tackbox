// Package fingerprint implements ERC006: a capture call's arguments must not
// leak raw user input into telemetry, and a tier-1 go/report Error/Warn
// call must carry a well-formed dedupKey.
//
// A capture is recognized by ORIGIN, not by name: the callee must resolve
// (type info) to the go/report package (Error/Warn/Panic) or to a
// `.tackbox/reporters`-declared sink. A bare local `Error` that shares
// the name but not the origin is not a capture and is never scanned.
//
// Three checks, mirroring the retired opengrep rules:
//   - user-input (any recognized reporter): no argument may carry raw
//     *http.Request input (`r.URL.Path`, `r.Header.Get(...)`, `r.Body`).
//   - msg-static (user-lane verbs Error/Warn/Notify - D007): the 2nd arg
//     (msg) must be a static string literal.
//   - dedupkey (Error/Warn/Quiet/Notify - D008, the known 5-arg signature):
//     the call must pass 5 args and the 5th must be a string literal matching
//     area.suffix[:id]. Notify is validated here but is never a capture.
package fingerprint

import (
	"go/ast"
	"go/token"
	"go/types"
	"regexp"
	"strconv"

	"golang.org/x/tools/go/analysis"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

var Analyzer = &analysis.Analyzer{
	Name: "fingerprint",
	Doc:  "ERC006: capture args must not leak raw user input; dedupKey must be a well-formed literal",
	Run:  run,
}

// dedupKeyRE is area.suffix[:id], lowercase, single optional `:identifier`.
var dedupKeyRE = regexp.MustCompile(`^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_-]*(:[a-zA-Z0-9_.-]+)?$`)

func run(pass *analysis.Pass) (interface{}, error) {
	astutil.InspectNonDeclared(pass, func(_ *ast.File) func(ast.Node) bool {
		return func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			if astutil.IsReporterCall(pass.TypesInfo, call) {
				for _, arg := range call.Args {
					checkArg(pass, arg)
				}
			}
			if astutil.IsReportMsgVerb(pass.TypesInfo, call) {
				checkMsg(pass, call)
			}
			if astutil.IsReportDedupVerb(pass.TypesInfo, call) {
				checkDedupKey(pass, call)
			}
			return true
		}
	})
	return nil, nil
}

// checkArg reports raw *http.Request input found anywhere in one argument's
// subtree.
func checkArg(pass *analysis.Pass, arg ast.Expr) {
	if desc, ok := userInput(pass.TypesInfo, arg); ok {
		pass.Reportf(arg.Pos(), "ERC006: capture arg carries raw *http.Request input (%s)", desc)
	}
}

// userInput returns a description of the first raw *http.Request expression in
// arg. Detection is type-aware: the receiver must resolve to *http.Request, so
// a same-shaped selector on an unrelated type (`cfg.URL.Path`) stays clean.
func userInput(info *types.Info, arg ast.Expr) (string, bool) {
	var desc string
	found := false
	ast.Inspect(arg, func(n ast.Node) bool {
		if found {
			return false
		}
		if d, ok := httpRequestInput(info, n); ok {
			desc, found = d, true
			return false
		}
		return true
	})
	return desc, found
}

// httpRequestInput matches r.URL.Path, r.Body, and r.Header.Get(...) where the
// receiver r resolves to *http.Request, under any receiver name.
func httpRequestInput(info *types.Info, n ast.Node) (string, bool) {
	switch x := n.(type) {
	case *ast.SelectorExpr:
		if x.Sel.Name == "Body" && isHTTPRequest(info, x.X) {
			return "r.Body", true
		}
		if x.Sel.Name == "Path" {
			if inner, ok := x.X.(*ast.SelectorExpr); ok && inner.Sel.Name == "URL" && isHTTPRequest(info, inner.X) {
				return "r.URL.Path", true
			}
		}
	case *ast.CallExpr:
		sel, ok := x.Fun.(*ast.SelectorExpr)
		if !ok || sel.Sel.Name != "Get" {
			return "", false
		}
		if inner, ok := sel.X.(*ast.SelectorExpr); ok && inner.Sel.Name == "Header" && isHTTPRequest(info, inner.X) {
			return "r.Header.Get(...)", true
		}
	}
	return "", false
}

func isHTTPRequest(info *types.Info, expr ast.Expr) bool {
	t := info.TypeOf(expr)
	if t == nil {
		return false
	}
	if ptr, ok := t.(*types.Pointer); ok {
		t = ptr.Elem()
	}
	named, ok := t.(*types.Named)
	if !ok {
		return false
	}
	obj := named.Obj()
	return obj != nil && obj.Pkg() != nil && obj.Pkg().Path() == "net/http" && obj.Name() == "Request"
}

// checkMsg enforces D007: the msg argument (2nd of ctx, msg, err, tags,
// dedupKey) of a user-lane verb (Error/Warn/Notify) must be a static string
// literal - it is what the user sees and what titles the issue; dynamic data
// belongs in cause and tags.
func checkMsg(pass *analysis.Pass, call *ast.CallExpr) {
	if len(call.Args) < 2 {
		return // wrong arity is checkDedupKey's finding
	}
	msg := call.Args[1]
	lit, ok := msg.(*ast.BasicLit)
	if !ok || lit.Kind != token.STRING {
		pass.Reportf(msg.Pos(),
			"ERC006: msg must be a static string literal (dynamic data belongs in cause and tags)")
	}
}

// checkDedupKey enforces the tier-1 dedupKey contract: exactly 5 args and a
// 5th arg that is a string literal matching area.suffix[:id].
func checkDedupKey(pass *analysis.Pass, call *ast.CallExpr) {
	if len(call.Args) != 5 {
		pass.Reportf(call.Pos(), "ERC006: capture call must pass 5 args (ctx, msg, err, tags, dedupKey)")
		return
	}
	key := call.Args[4]
	lit, ok := key.(*ast.BasicLit)
	if !ok || lit.Kind != token.STRING {
		pass.Reportf(key.Pos(), "ERC006: dedupKey must be a string literal")
		return
	}
	// lit is a parser-validated string literal: Unquote cannot fail.
	val, _ := strconv.Unquote(lit.Value)
	if !dedupKeyRE.MatchString(val) {
		pass.Reportf(key.Pos(), "ERC006: dedupKey must match area.suffix[:id] (got %q)", val)
	}
}

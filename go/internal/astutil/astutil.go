// Package astutil holds small AST helpers shared across analyzers.
package astutil

import (
	"go/ast"
	"go/token"
	"go/types"
	"strconv"
	"strings"

	"golang.org/x/tools/go/analysis"
)

// errorType is the built-in error interface, used to gate err-branches on the
// guarded identifier's static type.
var errorType = types.Universe.Lookup("error").Type().Underlying().(*types.Interface)

// reportPkgPath is the canonical capture-helper package. A call is a
// capture only when its callee resolves (type info) into this package -
// name-only matching is dead.
const reportPkgPath = "github.com/nikitatsym/tackbox/go/report"

// Capture tables are package-gated: an export of go/report counts only by
// this explicit list, never by signature inference. error-capture conflicts
// with `return err` (ERC005); panic-capture is terminal and does not.
var reportErrCapture = map[string]bool{"SentryErr": true, "Warn": true}
var reportPanicCapture = map[string]bool{"Panic": true}

// DeclaredReporter is a `.tackbox-reporters` sink resolved to its package
// path and function name. A call to it captures when the caught error flows
// into the call's arguments (argument-flow).
type DeclaredReporter struct {
	PkgPath string
	Name    string
}

var declaredReporters []DeclaredReporter

// SetDeclaredReporters installs the resolved declaration set; called once at
// startup before analysis.
func SetDeclaredReporters(ds []DeclaredReporter) { declaredReporters = ds }

type capKind int

const (
	capNone capKind = iota
	capErr
	capPanic
)

func captureKind(info *types.Info, call *ast.CallExpr, errName string) capKind {
	fn, ok := calleeFunc(info, call)
	if !ok || fn.Pkg() == nil {
		return capNone
	}
	if fn.Pkg().Path() == reportPkgPath {
		if reportErrCapture[fn.Name()] {
			return capErr
		}
		if reportPanicCapture[fn.Name()] {
			return capPanic
		}
		return capNone
	}
	for _, d := range declaredReporters {
		if d.PkgPath == fn.Pkg().Path() && d.Name == fn.Name() && argFlows(call, errName) {
			return capErr
		}
	}
	return capNone
}

// calleeFunc resolves call's callee to the *types.Func it denotes.
func calleeFunc(info *types.Info, call *ast.CallExpr) (*types.Func, bool) {
	if info == nil {
		return nil, false
	}
	switch fun := call.Fun.(type) {
	case *ast.Ident:
		if fn, ok := info.Uses[fun].(*types.Func); ok {
			return fn, true
		}
	case *ast.SelectorExpr:
		if fn, ok := info.Uses[fun.Sel].(*types.Func); ok {
			return fn, true
		}
	}
	return nil, false
}

func argFlows(call *ast.CallExpr, errName string) bool {
	if errName == "" {
		return false
	}
	for _, arg := range call.Args {
		if ContainsIdent(arg, errName) {
			return true
		}
	}
	return false
}

// ArgFlows reports whether name appears anywhere in call's arguments - the
// argument-flow primitive: a reported death (ERC003) or reported recover
// (ERC007) requires the caught value to reach the call.
func ArgFlows(call *ast.CallExpr, name string) bool { return argFlows(call, name) }

// IsCaptureErr reports whether call is an error-capture (excludes terminal
// panic-capture); doublecapture uses it to gate against `return err`.
func IsCaptureErr(info *types.Info, call *ast.CallExpr, errName string) bool {
	return captureKind(info, call, errName) == capErr
}

// CalleeName extracts the final identifier name of a call's Fun
// expression. Returns "" for unsupported shapes (function values,
// method values on non-trivial receivers, etc.).
func CalleeName(e ast.Expr) string {
	switch e := e.(type) {
	case *ast.Ident:
		return e.Name
	case *ast.SelectorExpr:
		return e.Sel.Name
	}
	return ""
}

// QualifiedName returns "pkg.Name" for selector calls and "Name" for
// bare idents. Used by analyzers that need to distinguish e.g.
// json.Unmarshal from yaml.Unmarshal.
func QualifiedName(e ast.Expr) string {
	if sel, ok := e.(*ast.SelectorExpr); ok {
		if id, ok := sel.X.(*ast.Ident); ok {
			return id.Name + "." + sel.Sel.Name
		}
	}
	if id, ok := e.(*ast.Ident); ok {
		return id.Name
	}
	return ""
}

// IsCapture reports whether call is a capture (error or panic capture).
func IsCapture(info *types.Info, call *ast.CallExpr, errName string) bool {
	return captureKind(info, call, errName) != capNone
}

// IsTestFile reports whether the file lives in *_test.go.
func IsTestFile(pass *analysis.Pass, f *ast.File) bool {
	pos := pass.Fset.File(f.Pos())
	if pos == nil {
		return false
	}
	return strings.HasSuffix(pos.Name(), "_test.go")
}

// IsExcluded reports whether the file lives in a vendored or
// third-party directory we never want to lint: node_modules, vendor,
// dist, build.
func IsExcluded(pass *analysis.Pass, f *ast.File) bool {
	pos := pass.Fset.File(f.Pos())
	if pos == nil {
		return false
	}
	name := pos.Name()
	for _, frag := range []string{"/node_modules/", "/vendor/", "/dist/", "/build/", "/.git/"} {
		if strings.Contains(name, frag) {
			return true
		}
	}
	return false
}

// IsGenerated reports whether the file carries the standard
// `// Code generated ... DO NOT EDIT` header — cgo wrappers,
// protoc output, stringer, etc. Such files are not in scope for
// error-reporting coverage.
func IsGenerated(f *ast.File) bool {
	for _, cg := range f.Comments {
		for _, c := range cg.List {
			text := strings.TrimSpace(strings.TrimPrefix(c.Text, "//"))
			if strings.HasPrefix(text, "Code generated") && strings.Contains(text, "DO NOT EDIT") {
				return true
			}
		}
	}
	return false
}

// EachFile invokes fn for every non-test, non-generated, in-project
// file.
func EachFile(pass *analysis.Pass, fn func(f *ast.File)) {
	for _, f := range pass.Files {
		if IsTestFile(pass, f) || IsGenerated(f) || IsExcluded(pass, f) {
			continue
		}
		fn(f)
	}
}

// ContainsIdent reports whether expr's subtree mentions an Ident
// with the given name.
func ContainsIdent(expr ast.Node, name string) bool {
	found := false
	ast.Inspect(expr, func(n ast.Node) bool {
		if id, ok := n.(*ast.Ident); ok && id.Name == name {
			found = true
			return false
		}
		return !found
	})
	return found
}

// ErrIdentFromIfCond returns the error identifier name from a
// canonical `err != nil` condition, or "" if cond is not that shape.
// It accepts either `err != nil` or `nil != err`.
func ErrIdentFromIfCond(cond ast.Expr) string {
	if id, ok := ErrIdentExprFromIfCond(cond); ok {
		return id.Name
	}
	return ""
}

// ErrIdentExprFromIfCond returns the identifier node from a canonical
// `x != nil` condition (either order); ok is false if cond is not that shape.
func ErrIdentExprFromIfCond(cond ast.Expr) (*ast.Ident, bool) {
	bin, ok := cond.(*ast.BinaryExpr)
	if !ok || bin.Op != token.NEQ {
		return nil, false
	}
	if id, ok := bin.X.(*ast.Ident); ok && isNil(bin.Y) {
		return id, true
	}
	if id, ok := bin.Y.(*ast.Ident); ok && isNil(bin.X) {
		return id, true
	}
	return nil, false
}

func isNil(e ast.Expr) bool {
	id, ok := e.(*ast.Ident)
	return ok && id.Name == "nil"
}

// IsErrorAssignable reports whether ident's static type is assignable to the
// built-in error interface - an error value or a concrete error implementation
// (e.g. *ParseError). A plain `type == error` check would miss the latter.
func IsErrorAssignable(info *types.Info, ident *ast.Ident) bool {
	if info == nil {
		return false
	}
	t := info.TypeOf(ident)
	if t == nil {
		return false
	}
	return types.Implements(t, errorType) || types.AssignableTo(t, errorType)
}

// BlockPropagatesChain reports whether any top-level return in body carries
// errName into the returned error without breaking the unwrap chain: a bare
// `return err`, a `fmt.Errorf(<...%w...>, ..., err)`, or `errors.Join(..., err)`.
// A `%v` / `.Error()` stringification breaks the chain and does not count.
func BlockPropagatesChain(body *ast.BlockStmt, errName string) bool {
	for _, ret := range BlockReturns(body) {
		if ReturnChainPreserves(ret, errName) {
			return true
		}
	}
	return false
}

// ReturnChainPreserves reports whether ret carries errName chain-preservingly.
func ReturnChainPreserves(ret *ast.ReturnStmt, errName string) bool {
	if errName == "" {
		return false
	}
	for _, res := range ret.Results {
		if chainPreservingCarrier(res, errName) {
			return true
		}
	}
	return false
}

func chainPreservingCarrier(res ast.Expr, errName string) bool {
	if id, ok := res.(*ast.Ident); ok { // bare `return err`, unwrapped
		return id.Name == errName
	}
	call, ok := res.(*ast.CallExpr)
	if !ok {
		return false
	}
	switch QualifiedName(call.Fun) {
	case "errors.Join":
		return argFlows(call, errName)
	case "fmt.Errorf":
		return wrapsWithVerbW(call, errName)
	}
	return false
}

// wrapsWithVerbW reports whether a fmt.Errorf call wraps errName with the %w
// verb: a string-literal format carrying %w, and errName flowing into the
// arguments. A non-literal format cannot be verified and does not count.
func wrapsWithVerbW(call *ast.CallExpr, errName string) bool {
	if len(call.Args) == 0 {
		return false
	}
	lit, ok := call.Args[0].(*ast.BasicLit)
	if !ok || lit.Kind != token.STRING {
		return false
	}
	format, err := strconv.Unquote(lit.Value)
	if err != nil || !containsVerbW(format) {
		return false
	}
	return argFlows(call, errName)
}

// containsVerbW reports whether format contains a %w verb, skipping escaped %%.
func containsVerbW(format string) bool {
	for i := 0; i < len(format); i++ {
		if format[i] != '%' {
			continue
		}
		i++
		if i >= len(format) {
			return false
		}
		if format[i] == '%' {
			continue // escaped percent
		}
		for i < len(format) && strings.ContainsRune("+-# 0.123456789*", rune(format[i])) {
			i++ // flags, width, precision
		}
		if i < len(format) && format[i] == 'w' {
			return true
		}
	}
	return false
}

// BlockCalls walks block and reports any direct CallExpr inside its
// statements (top-level only, not nested function literals).
func BlockCalls(block *ast.BlockStmt) []*ast.CallExpr {
	var out []*ast.CallExpr
	for _, st := range block.List {
		ast.Inspect(st, func(n ast.Node) bool {
			if _, ok := n.(*ast.FuncLit); ok {
				return false
			}
			if call, ok := n.(*ast.CallExpr); ok {
				out = append(out, call)
			}
			return true
		})
	}
	return out
}

// BlockReturns lists the return statements inside block, excluding
// returns inside nested function literals.
func BlockReturns(block *ast.BlockStmt) []*ast.ReturnStmt {
	var out []*ast.ReturnStmt
	for _, st := range block.List {
		ast.Inspect(st, func(n ast.Node) bool {
			if _, ok := n.(*ast.FuncLit); ok {
				return false
			}
			if r, ok := n.(*ast.ReturnStmt); ok {
				out = append(out, r)
			}
			return true
		})
	}
	return out
}

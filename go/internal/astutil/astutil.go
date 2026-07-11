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
// path and function name. A capture sink's call captures when the caught
// error flows into the call's arguments (argument-flow). A usage sink
// (`[usage]`) never captures; ERC003 owns its semantics.
type DeclaredReporter struct {
	PkgPath string
	Name    string
	Usage   bool
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
		if d.Usage {
			continue
		}
		if d.PkgPath == fn.Pkg().Path() && d.Name == fn.Name() && argFlows(call, errName) {
			return capErr
		}
	}
	return capNone
}

// IsUsageSink reports whether call's callee resolves to a declared usage sink.
func IsUsageSink(info *types.Info, call *ast.CallExpr) bool {
	fn, ok := calleeFunc(info, call)
	if !ok || fn.Pkg() == nil {
		return false
	}
	for _, d := range declaredReporters {
		if d.Usage && d.PkgPath == fn.Pkg().Path() && d.Name == fn.Name() {
			return true
		}
	}
	return false
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

// IsDeclaredBody reports whether fn is a declared reporter: its body is the
// trust boundary, reviewed at declaration time - analyzers do not look
// inside. A die-helper's own os.Exit is what the declaration vouches for.
func IsDeclaredBody(info *types.Info, fn *ast.FuncDecl) bool {
	if info == nil || fn.Name == nil {
		return false
	}
	obj, ok := info.Defs[fn.Name].(*types.Func)
	if !ok || obj.Pkg() == nil {
		return false
	}
	for _, d := range declaredReporters {
		if d.PkgPath == obj.Pkg().Path() && d.Name == obj.Name() {
			return true
		}
	}
	return false
}

// ErrAliases returns errName plus every identifier bound to the same error
// object via `errors.As(errName, &x)` within body: x IS the guarded error,
// so capture or argument-flow through x is capture of the guarded error.
func ErrAliases(body *ast.BlockStmt, errName string) []string {
	names := []string{errName}
	if errName == "" || body == nil {
		return names
	}
	ast.Inspect(body, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok || QualifiedName(call.Fun) != "errors.As" || len(call.Args) != 2 {
			return true
		}
		src, ok := call.Args[0].(*ast.Ident)
		if !ok || src.Name != errName {
			return true
		}
		if ue, ok := call.Args[1].(*ast.UnaryExpr); ok && ue.Op == token.AND {
			if id, ok := ue.X.(*ast.Ident); ok {
				names = append(names, id.Name)
			}
		}
		return true
	})
	return names
}

// IsPrintingTerminal reports whether call is a terminal that prints its
// arguments (`log.Fatal*` or the in-repo `die`). os.Exit is excluded: it
// prints nothing, so carrying the error into it reports nothing.
func IsPrintingTerminal(call *ast.CallExpr) bool {
	if strings.HasPrefix(QualifiedName(call.Fun), "log.Fatal") {
		return true
	}
	id, ok := call.Fun.(*ast.Ident)
	return ok && id.Name == "die"
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
// `// Code generated ... DO NOT EDIT` header - cgo wrappers,
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

// InspectNonDeclared walks each in-project file's AST, skipping the bodies of
// externally-declared functions (asm/linkname stubs have no analyzable body).
// perFile runs once per file and returns the node visitor, so per-file state
// (a markers.Index) is built there rather than on every node.
func InspectNonDeclared(pass *analysis.Pass, perFile func(f *ast.File) func(ast.Node) bool) {
	EachFile(pass, func(f *ast.File) {
		visit := perFile(f)
		ast.Inspect(f, func(n ast.Node) bool {
			if fn, ok := n.(*ast.FuncDecl); ok && IsDeclaredBody(pass.TypesInfo, fn) {
				return false
			}
			return visit(n)
		})
	})
}

// EachTestFile invokes fn for every in-project *_test.go file - the
// mirror of EachFile for rules whose subject is the tests themselves.
func EachTestFile(pass *analysis.Pass, fn func(f *ast.File)) {
	for _, f := range pass.Files {
		if !IsTestFile(pass, f) || IsGenerated(f) || IsExcluded(pass, f) {
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

// IsErrorAssignableExpr reports whether expr's static type is assignable to the
// built-in error interface - an error value or a concrete error implementation
// (e.g. *ParseError). A plain `type == error` check would miss the latter. The
// type-gate uses it on the guarded identifier.
func IsErrorAssignableExpr(info *types.Info, expr ast.Expr) bool {
	if info == nil {
		return false
	}
	return typeIsErrorAssignable(info.TypeOf(expr))
}

func typeIsErrorAssignable(t types.Type) bool {
	if t == nil {
		return false
	}
	return types.Implements(t, errorType) || types.AssignableTo(t, errorType)
}

// IsErrorCarryingExpr reports whether expr can hand an error to the caller:
// its type is error-assignable, or it is a multi-value call with an
// error-assignable component. Arity must not matter - `return 0, wrap(err)`
// and `return wrap2(err)` are the same carrier shape.
func IsErrorCarryingExpr(info *types.Info, expr ast.Expr) bool {
	if info == nil {
		return false
	}
	t := info.TypeOf(expr)
	if tup, ok := t.(*types.Tuple); ok {
		for i := 0; i < tup.Len(); i++ {
			if typeIsErrorAssignable(tup.At(i).Type()) {
				return true
			}
		}
		return false
	}
	return typeIsErrorAssignable(t)
}

// BlockPropagatesChain reports whether the err-branch body carries the err
// OBJECT into a returned error-assignable expression without stringifying every
// occurrence. Object flow through a composite literal, a constructor call, a
// `%w` wrap, errors.Join, or a bare `return err` is propagation - a
// constructor's Unwrap contract is trusted (trust-class die/declared), not
// verified. The chain breaks only when every occurrence of err in the carrier
// passes through a string (`.Error()`, a non-%w verb, a string conversion). A
// two-step wrap (`v := ...%w...; return v`) is resolved against body.
func BlockPropagatesChain(info *types.Info, body *ast.BlockStmt, errName string) bool {
	if errName == "" {
		return false
	}
	for _, ret := range BlockReturns(body) {
		for _, res := range ret.Results {
			if returnResultPropagates(info, body, res, errName) {
				return true
			}
		}
	}
	return false
}

// returnResultPropagates reports whether one returned result carries the err
// object onward. A bare local carrier (`v` from `v := <wrap>`) is resolved to
// its assignment in body first, crediting a two-step wrap. A tuple-returning
// call with an error component (`return fail(err)`) is the same carrier shape
// as `return 0, wrap(err)` - the callee is trusted, not resolved.
func returnResultPropagates(info *types.Info, body *ast.BlockStmt, res ast.Expr, errName string) bool {
	carrier := res
	if id, ok := res.(*ast.Ident); ok && id.Name != errName {
		if rhs, ok := localAssignRHS(body, id.Name); ok {
			carrier = rhs
		}
	}
	if !IsErrorCarryingExpr(info, carrier) {
		return false
	}
	return errObjectFlows(carrier, errName)
}

// errObjectFlows reports whether errName reaches root as a live object: found
// as a bare identifier outside any stringifying construct. Subtrees that
// stringify their content (`.Error()`, a fmt string builder, a %w-less
// fmt.Errorf, a `string(...)` conversion) are pruned - an err inside them is a
// stringified occurrence, not object flow.
func errObjectFlows(root ast.Node, errName string) bool {
	found := false
	ast.Inspect(root, func(n ast.Node) bool {
		if found {
			return false
		}
		switch x := n.(type) {
		case *ast.CallExpr:
			if stringifies(x) {
				return false
			}
		case *ast.Ident:
			if x.Name == errName {
				found = true
				return false
			}
		}
		return true
	})
	return found
}

// stringifies reports whether call converts its arguments to a string, so an
// err inside is a stringified occurrence: a `.Error()` method call, a fmt
// string builder, a %w-less fmt.Errorf, or a `string(...)` conversion. A
// %w-carrying fmt.Errorf wraps and is not a stringifier.
func stringifies(call *ast.CallExpr) bool {
	if sel, ok := call.Fun.(*ast.SelectorExpr); ok && sel.Sel.Name == "Error" && len(call.Args) == 0 {
		return true
	}
	switch QualifiedName(call.Fun) {
	case "fmt.Sprintf", "fmt.Sprint", "fmt.Sprintln":
		return true
	case "fmt.Errorf":
		return !errorfWraps(call)
	case "string":
		return true
	}
	return false
}

// errorfWraps reports whether a fmt.Errorf call's literal format carries a %w
// verb. A non-literal format cannot be verified and is treated as non-wrapping.
func errorfWraps(call *ast.CallExpr) bool {
	if len(call.Args) == 0 {
		return false
	}
	lit, ok := call.Args[0].(*ast.BasicLit)
	if !ok || lit.Kind != token.STRING {
		return false
	}
	// lit is a parser-validated string literal: Unquote cannot fail; on the
	// impossible error an empty format carries no %w (fail closed).
	format, _ := strconv.Unquote(lit.Value)
	return containsVerbW(format)
}

// localAssignRHS returns the right-hand side of the last top-level assignment
// to name in body, for resolving a two-step wrap in an err-branch.
func localAssignRHS(body *ast.BlockStmt, name string) (ast.Expr, bool) {
	var rhs ast.Expr
	found := false
	for _, st := range body.List {
		assign, ok := st.(*ast.AssignStmt)
		if !ok || len(assign.Lhs) != 1 || len(assign.Rhs) != 1 {
			continue
		}
		id, ok := assign.Lhs[0].(*ast.Ident)
		if !ok || id.Name != name {
			continue
		}
		rhs, found = assign.Rhs[0], true
	}
	return rhs, found
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

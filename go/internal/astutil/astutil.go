// Package astutil holds small AST helpers shared across analyzers.
package astutil

import (
	"go/ast"
	"go/token"
	"strings"

	"golang.org/x/tools/go/analysis"
)

// CaptureNames are function names that count as a capture site.
// Match is on the last identifier of the call expression, so both
// bare `sentryErr(...)` and `report.SentryErr(...)` are matched.
var CaptureNames = map[string]bool{
	"sentryErr": true, // gmux-style unexported helper
	"SentryErr": true, // exported (e.g. report.SentryErr)
	"Warn":      true,
	"Panic":     true,
}

// CaptureErrNames is CaptureNames minus Panic — used by
// doublecapture to distinguish error-capture (which conflicts with
// `return err`) from terminal panic-capture.
var CaptureErrNames = map[string]bool{
	"sentryErr": true,
	"SentryErr": true,
	"Warn":      true,
}

// IsCaptureErr reports whether call is an error-capture invocation
// (SentryErr / sentryErr / Warn). Excludes Panic.
func IsCaptureErr(call *ast.CallExpr) bool {
	return CaptureErrNames[CalleeName(call.Fun)]
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

// IsCapture reports whether call is a capture invocation by name.
func IsCapture(call *ast.CallExpr) bool {
	return CaptureNames[CalleeName(call.Fun)]
}

// IsTestFile reports whether the file lives in *_test.go.
func IsTestFile(pass *analysis.Pass, f *ast.File) bool {
	pos := pass.Fset.File(f.Pos())
	if pos == nil {
		return false
	}
	return strings.HasSuffix(pos.Name(), "_test.go")
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

// EachFile invokes fn for every non-test, non-generated file.
func EachFile(pass *analysis.Pass, fn func(f *ast.File)) {
	for _, f := range pass.Files {
		if IsTestFile(pass, f) || IsGenerated(f) {
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
	bin, ok := cond.(*ast.BinaryExpr)
	if !ok || bin.Op != token.NEQ {
		return ""
	}
	if id, ok := bin.X.(*ast.Ident); ok && isNil(bin.Y) {
		return id.Name
	}
	if id, ok := bin.Y.(*ast.Ident); ok && isNil(bin.X) {
		return id.Name
	}
	return ""
}

func isNil(e ast.Expr) bool {
	id, ok := e.(*ast.Ident)
	return ok && id.Name == "nil"
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

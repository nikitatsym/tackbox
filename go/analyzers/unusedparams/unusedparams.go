// Copyright 2020 The Go Authors. All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

// Package unusedparams adapts gopls v0.23.0's unusedparams analyzer for ERC011.
package unusedparams

import (
	"fmt"
	"go/ast"
	"go/types"
	"strings"

	"golang.org/x/tools/go/analysis"
	"golang.org/x/tools/go/analysis/passes/inspect"
	"golang.org/x/tools/go/ast/inspector"
)

var Analyzer = &analysis.Analyzer{
	Name:     "unusedparams",
	Doc:      "ERC011: remove unused parameters of unexported, directly called functions",
	Requires: []*analysis.Analyzer{inspect.Analyzer},
	Run:      run,
}

// EmitJSON is set by the JSON driver before analysis starts. A nil diagnostic
// records an internal test package even when it has no parameter findings.
var EmitJSON func(*analysis.Pass, string, *analysis.Diagnostic)

const remedy = "remove this parameter and its argument at every call site in this package; preserve argument side effects and evaluation order; use `_` only if another build variant uses the parameter"

func run(pass *analysis.Pass) (any, error) {
	inspect := pass.ResultOf[inspect.Analyzer].(*inspector.Inspector)
	variant := "plain"
	abi := make(map[string]bool)
	hasProduction := false
	for _, file := range pass.Files {
		if strings.HasSuffix(pass.Fset.Position(file.Pos()).Filename, "_test.go") {
			variant = "internal-test"
		} else {
			hasProduction = true
		}
		for _, group := range file.Comments {
			for _, comment := range group.List {
				fields := strings.Fields(comment.Text)
				if len(fields) >= 2 && fields[0] == "//go:linkname" {
					abi[fields[1]] = true
				}
			}
		}
	}
	if variant == "internal-test" && !hasProduction && strings.HasSuffix(pass.Pkg.Name(), "_test") {
		variant = "external-test"
	}
	if EmitJSON != nil && variant == "internal-test" {
		EmitJSON(pass, variant, nil)
	}

	// Signatures of function values and interface methods may be fixed elsewhere.
	usesOutsideCall := make(map[*types.Func]bool)
	hasUses := make(map[*types.Func]bool)
	unexportedIMethodNames := make(map[string]bool)
	callPosn := make(map[*ast.Ident]bool)
	inspect.Preorder([]ast.Node{(*ast.CallExpr)(nil), (*ast.InterfaceType)(nil)}, func(n ast.Node) {
		switch n := n.(type) {
		case *ast.CallExpr:
			if id, ok := usedIdent(pass.TypesInfo, n.Fun); ok {
				callPosn[id] = true
			}
		case *ast.InterfaceType:
			// Matching by name also excludes methods with different signatures.
			for method := range pass.TypesInfo.TypeOf(n).(*types.Interface).ExplicitMethods() {
				if !method.Exported() && method.Name() != "_" {
					unexportedIMethodNames[method.Name()] = true
				}
			}
		}
	})
	usedVars := make(map[*types.Var]bool)
	for id, obj := range pass.TypesInfo.Uses {
		switch obj := obj.(type) {
		case *types.Func:
			fn := obj.Origin()
			hasUses[fn] = true
			if !callPosn[id] {
				usesOutsideCall[fn] = true
			}
		case *types.Var:
			if !obj.IsField() {
				usedVars[obj] = true
			}
		}
	}

	for c := range inspect.Root().Preorder((*ast.FuncDecl)(nil)) {
		n := c.Node().(*ast.FuncDecl)
		if n.Body == nil || n.Name.IsExported() || abi[n.Name.Name] || fixedABI(n) {
			continue
		}
		if n.Recv != nil && unexportedIMethodNames[n.Name.Name] {
			continue
		}
		fn := pass.TypesInfo.Defs[n.Name].(*types.Func).Origin()
		// Leave declarations with no references to U1000.
		if !hasUses[fn] || usesOutsideCall[fn] || n.Type.Params.NumFields() == 0 {
			continue
		}
		switch len(n.Body.List) {
		case 0:
			continue
		case 1:
			if stmt, ok := n.Body.List[0].(*ast.ExprStmt); ok {
				if call, ok := stmt.X.(*ast.CallExpr); ok {
					if fun, ok := call.Fun.(*ast.Ident); ok && fun.Name == "panic" {
						continue
					}
				}
			}
		}
		file := c
		for parent := range c.Enclosing((*ast.File)(nil)) {
			file = parent
			break
		}
		if ast.IsGenerated(file.Node().(*ast.File)) {
			continue
		}
		for _, field := range n.Type.Params.List {
			for _, id := range field.Names {
				if id.Name == "_" || usedVars[pass.TypesInfo.Defs[id].(*types.Var)] {
					continue
				}
				start, end := field.Pos(), field.End()
				if len(field.Names) > 1 {
					start, end = id.Pos(), id.End()
				}
				diagnostic := analysis.Diagnostic{
					Pos: start, End: end,
					Message: fmt.Sprintf("ERC011: unused parameter %s: %s", id.Name, remedy),
				}
				if EmitJSON != nil {
					EmitJSON(pass, variant, &diagnostic)
				} else {
					pass.Report(diagnostic)
				}
			}
		}
	}
	return nil, nil
}

func fixedABI(n *ast.FuncDecl) bool {
	if n.Doc != nil {
		for _, comment := range n.Doc.List {
			fields := strings.Fields(comment.Text)
			if len(fields) >= 2 && (fields[0] == "//export" || fields[0] == "//go:wasmexport") {
				return true
			}
		}
	}
	return false
}

// Copyright 2018 The Go Authors. All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

// usedIdent is adapted from x/tools internal/typesinternal.UsedIdent.
func usedIdent(info *types.Info, e ast.Expr) (*ast.Ident, bool) {
	switch d := ast.Unparen(e).(type) {
	case *ast.IndexExpr:
		if info.Types[d.Index].IsType() {
			e = d.X
		}
	case *ast.IndexListExpr:
		e = d.X
	}
	switch e := ast.Unparen(e).(type) {
	case *ast.Ident:
		return e, true
	case *ast.SelectorExpr:
		return e.Sel, true
	}
	return nil, false
}

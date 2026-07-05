// Package returnnil implements ERC004: a bare `return nil` from a
// function whose single result is `*T`, `[]T`, or `map[K]V` must
// carry a `// nil-return: <reason>` marker on the line directly
// above. The alternative is to widen the signature to
// `(val, ok)` / `(val, err)`. Error-assignable results are exempt:
// their nil is the no-error contract.
package returnnil

import (
	"go/ast"

	"golang.org/x/tools/go/analysis"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
	"github.com/nikitatsym/tackbox/go/internal/markers"
)

var Analyzer = &analysis.Analyzer{
	Name: "returnnil",
	Doc:  "ERC004: bare `return nil` needs `// nil-return:` marker or wider signature",
	Run:  run,
}

func run(pass *analysis.Pass) (interface{}, error) {
	astutil.EachFile(pass, func(f *ast.File) {
		idx := markers.Build(pass.Fset, f)
		for _, decl := range f.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok || fn.Body == nil {
				continue
			}
			if astutil.IsDeclaredBody(pass.TypesInfo, fn) {
				continue
			}
			if !candidateSignature(fn.Type) {
				continue
			}
			// nil from an error-assignable result is the no-error contract,
			// not a hidden empty value; err-branch swallows stay on ERC001.
			if astutil.IsErrorAssignableExpr(pass.TypesInfo, fn.Type.Results.List[0].Type) {
				continue
			}
			checkBody(pass, idx, fn.Body)
		}
	})
	return nil, nil
}

func checkBody(pass *analysis.Pass, idx *markers.Index, body *ast.BlockStmt) {
	ast.Inspect(body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		ret, ok := n.(*ast.ReturnStmt)
		if !ok {
			return true
		}
		if !isReturnNil(ret) {
			return true
		}
		if m, ok := idx.Above(ret); ok && m.Kind == markers.NilReturn {
			return true
		}
		pass.Reportf(ret.Pos(),
			"ERC004: bare `return nil` requires `// nil-return: <reason>` marker or wider signature `(val, ok)` / `(val, err)`")
		return true
	})
}

func candidateSignature(ft *ast.FuncType) bool {
	if ft.Results == nil || len(ft.Results.List) != 1 {
		return false
	}
	field := ft.Results.List[0]
	if len(field.Names) > 1 {
		return false
	}
	switch t := field.Type.(type) {
	case *ast.StarExpr:
		return true
	case *ast.ArrayType:
		return t.Len == nil
	case *ast.MapType:
		return true
	}
	return false
}

func isReturnNil(ret *ast.ReturnStmt) bool {
	if len(ret.Results) != 1 {
		return false
	}
	id, ok := ret.Results[0].(*ast.Ident)
	return ok && id.Name == "nil"
}

package markers

import (
	"go/ast"

	"golang.org/x/tools/go/analysis"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

// Runner adapts a per-node visitor into an analysis.Run: it builds the marker
// Index once per file, skips externally-declared bodies, and calls visit for
// every remaining node. Collapses the identical per-analyzer run scaffolding
// (EachFile + Build + declared-body skip) into one place.
func Runner(visit func(idx *Index, pass *analysis.Pass, n ast.Node) bool) func(*analysis.Pass) (interface{}, error) {
	return func(pass *analysis.Pass) (interface{}, error) {
		astutil.InspectNonDeclared(pass, func(f *ast.File) func(ast.Node) bool {
			idx := Build(pass.Fset, f)
			return func(n ast.Node) bool {
				return visit(idx, pass, n)
			}
		})
		return nil, nil
	}
}

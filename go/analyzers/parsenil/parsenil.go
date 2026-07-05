// Package parsenil implements ERC002: results from standard parsers
// must either be captured on the error path or carry a
// `// parse-skip: <reason>` marker. Skip reasons in the spec are
// limited to user-input, fallthrough, optional-config. The reasons
// `schema-drift` and `expected` indicate a real error and must
// capture, not skip.
package parsenil

import (
	"go/ast"
	"go/token"
	"go/types"
	"strings"

	"golang.org/x/tools/go/analysis"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
	"github.com/nikitatsym/tackbox/go/internal/markers"
)

var Analyzer = &analysis.Analyzer{
	Name: "parsenil",
	Doc:  "ERC002: parser errs must capture or carry `// parse-skip:` marker",
	Run:  run,
}

// Identifiers are matched syntactically by their qualified package
// name. Aliased imports or dot-imports are not resolved.
var parsers = map[string]bool{
	"json.Unmarshal":       true,
	"xml.Unmarshal":        true,
	"yaml.Unmarshal":       true,
	"time.Parse":           true,
	"time.ParseDuration":   true,
	"time.ParseInLocation": true,
	"strconv.Atoi":         true,
	"strconv.ParseInt":     true,
	"strconv.ParseFloat":   true,
	"strconv.ParseBool":    true,
	"url.Parse":            true,
	"regexp.Compile":       true,
	"netip.ParseAddr":      true,
	"net.ParseIP":          true,
}

// net.ParseIP returns a single value (net.IP) and signals failure
// by returning nil. Treated separately from error-returning parsers.
const parseIP = "net.ParseIP"

func run(pass *analysis.Pass) (interface{}, error) {
	astutil.EachFile(pass, func(f *ast.File) {
		idx := markers.Build(pass.Fset, f)
		ast.Inspect(f, func(n ast.Node) bool {
			if fn, ok := n.(*ast.FuncDecl); ok && astutil.IsDeclaredBody(pass.TypesInfo, fn) {
				return false
			}
			switch x := n.(type) {
			case *ast.BlockStmt:
				handleBlock(pass, idx, x)
			case *ast.IfStmt:
				handleIfShortForm(pass, idx, x)
			}
			return true
		})
	})
	return nil, nil
}

func handleBlock(pass *analysis.Pass, idx *markers.Index, block *ast.BlockStmt) {
	for i, st := range block.List {
		assign, ok := st.(*ast.AssignStmt)
		if !ok {
			continue
		}
		callee := parserCalleeFromAssign(assign)
		if callee == "" {
			continue
		}
		if callee == parseIP {
			handleParseIPAssign(pass, idx, assign, block.List[i+1:])
		} else {
			handleErrParserAssign(pass, idx, assign, callee, block.List[i+1:])
		}
	}
}

func handleIfShortForm(pass *analysis.Pass, idx *markers.Index, ifst *ast.IfStmt) {
	assign, ok := ifst.Init.(*ast.AssignStmt)
	if !ok {
		return
	}
	callee := parserCalleeFromAssign(assign)
	if callee == "" {
		return
	}
	if callee == parseIP {
		handleParseIPShort(pass, idx, ifst, assign)
	} else {
		handleErrParserShort(pass, idx, ifst, assign, callee)
	}
}

func parserCalleeFromAssign(assign *ast.AssignStmt) string {
	if len(assign.Rhs) != 1 {
		return ""
	}
	call, ok := assign.Rhs[0].(*ast.CallExpr)
	if !ok {
		return ""
	}
	callee := astutil.QualifiedName(call.Fun)
	if !parsers[callee] {
		return ""
	}
	return callee
}

func handleErrParserAssign(pass *analysis.Pass, idx *markers.Index, assign *ast.AssignStmt, callee string, rest []ast.Stmt) {
	if handleMarker(idx, assign, callee, pass) {
		return
	}
	errName := errIdentFromLHS(assign)
	if errName == "" {
		pass.Reportf(assign.Pos(),
			"ERC002: %s err discarded, requires `// parse-skip: <reason>` marker",
			callee)
		return
	}
	ifst, ok := nextIfErrNotNil(rest, errName)
	if !ok {
		pass.Reportf(assign.Pos(),
			"ERC002: %s err `%s` not checked, requires capture or `// parse-skip:` marker",
			callee, errName)
		return
	}
	if !errBranchHandled(pass.TypesInfo, ifst.Body, errName) {
		pass.Reportf(ifst.Pos(),
			"ERC002: %s err-branch must capture, propagate the error chain-preservingly, or carry `// parse-skip: <reason>` (err=%s)",
			callee, errName)
	}
}

func handleErrParserShort(pass *analysis.Pass, idx *markers.Index, ifst *ast.IfStmt, assign *ast.AssignStmt, callee string) {
	if handleMarker(idx, ifst, callee, pass) {
		return
	}
	errName := errIdentFromLHS(assign)
	if errName == "" {
		pass.Reportf(ifst.Pos(),
			"ERC002: %s err discarded in short form, requires `// parse-skip: <reason>` marker",
			callee)
		return
	}
	if astutil.ErrIdentFromIfCond(ifst.Cond) != errName {
		return
	}
	if !errBranchHandled(pass.TypesInfo, ifst.Body, errName) {
		pass.Reportf(ifst.Pos(),
			"ERC002: %s err-branch must capture, propagate the error chain-preservingly, or carry `// parse-skip: <reason>` (err=%s)",
			callee, errName)
	}
}

// errBranchHandled mirrors the ERC001 exits: capture, chain-preserving
// propagation, or a reported death (a printing terminal carrying the parse
// error) - checked through errors.As aliases of the guarded error.
func errBranchHandled(info *types.Info, body *ast.BlockStmt, errName string) bool {
	for _, name := range astutil.ErrAliases(body, errName) {
		if hasCaptureInBody(info, body, name) || astutil.BlockPropagatesChain(info, body, name) {
			return true
		}
		for _, call := range astutil.BlockCalls(body) {
			if astutil.IsPrintingTerminal(call) && astutil.ArgFlows(call, name) {
				return true
			}
		}
	}
	return false
}

func handleParseIPAssign(pass *analysis.Pass, idx *markers.Index, assign *ast.AssignStmt, rest []ast.Stmt) {
	if handleMarker(idx, assign, parseIP, pass) {
		return
	}
	valName := firstIdentLHS(assign)
	if valName == "" {
		pass.Reportf(assign.Pos(),
			"ERC002: net.ParseIP result discarded, requires `// parse-skip: <reason>` marker")
		return
	}
	ifst, ok := nextIfValEqNil(rest, valName)
	if !ok {
		pass.Reportf(assign.Pos(),
			"ERC002: net.ParseIP result `%s` not nil-checked, requires capture or `// parse-skip:` marker",
			valName)
		return
	}
	if !hasCaptureInBody(pass.TypesInfo, ifst.Body, valName) {
		pass.Reportf(ifst.Pos(),
			"ERC002: net.ParseIP nil-branch must capture or carry `// parse-skip: <reason>` (val=%s)",
			valName)
	}
}

func handleParseIPShort(pass *analysis.Pass, idx *markers.Index, ifst *ast.IfStmt, assign *ast.AssignStmt) {
	if handleMarker(idx, ifst, parseIP, pass) {
		return
	}
	valName := firstIdentLHS(assign)
	if valName == "" {
		pass.Reportf(ifst.Pos(),
			"ERC002: net.ParseIP result discarded in short form, requires `// parse-skip: <reason>` marker")
		return
	}
	if !condIsValEqNil(ifst.Cond, valName) {
		return
	}
	if !hasCaptureInBody(pass.TypesInfo, ifst.Body, valName) {
		pass.Reportf(ifst.Pos(),
			"ERC002: net.ParseIP nil-branch must capture or carry `// parse-skip: <reason>` (val=%s)",
			valName)
	}
}

func errIdentFromLHS(assign *ast.AssignStmt) string {
	n := len(assign.Lhs)
	if n == 0 || n > 2 {
		return ""
	}
	id, ok := assign.Lhs[n-1].(*ast.Ident)
	if !ok || id.Name == "_" {
		return ""
	}
	return id.Name
}

func firstIdentLHS(assign *ast.AssignStmt) string {
	if len(assign.Lhs) == 0 {
		return ""
	}
	id, ok := assign.Lhs[0].(*ast.Ident)
	if !ok || id.Name == "_" {
		return ""
	}
	return id.Name
}

func nextIfErrNotNil(rest []ast.Stmt, errName string) (*ast.IfStmt, bool) {
	if len(rest) == 0 {
		return nil, false
	}
	ifst, ok := rest[0].(*ast.IfStmt)
	if !ok {
		return nil, false
	}
	if astutil.ErrIdentFromIfCond(ifst.Cond) != errName {
		return nil, false
	}
	return ifst, true
}

func nextIfValEqNil(rest []ast.Stmt, valName string) (*ast.IfStmt, bool) {
	if len(rest) == 0 {
		return nil, false
	}
	ifst, ok := rest[0].(*ast.IfStmt)
	if !ok {
		return nil, false
	}
	if !condIsValEqNil(ifst.Cond, valName) {
		return nil, false
	}
	return ifst, true
}

func condIsValEqNil(cond ast.Expr, valName string) bool {
	bin, ok := cond.(*ast.BinaryExpr)
	if !ok || bin.Op != token.EQL {
		return false
	}
	if matchIdentVsNil(bin.X, bin.Y, valName) {
		return true
	}
	return matchIdentVsNil(bin.Y, bin.X, valName)
}

func matchIdentVsNil(side, other ast.Expr, name string) bool {
	id, ok := side.(*ast.Ident)
	if !ok || id.Name != name {
		return false
	}
	nilId, ok := other.(*ast.Ident)
	return ok && nilId.Name == "nil"
}

func hasCaptureInBody(info *types.Info, body *ast.BlockStmt, name string) bool {
	for _, call := range astutil.BlockCalls(body) {
		if astutil.IsCapture(info, call, name) {
			return true
		}
	}
	return false
}

func handleMarker(idx *markers.Index, node ast.Node, callee string, pass *analysis.Pass) bool {
	m, ok := idx.Above(node)
	if !ok || m.Kind != markers.ParseSkip {
		return false
	}
	if reasonRequiresCapture(m.Reason) {
		pass.Reportf(m.Pos,
			"ERC002: %s with `parse-skip: %s` indicates a real error and must capture instead",
			callee, m.Reason)
	}
	return true
}

func reasonRequiresCapture(reason string) bool {
	fields := strings.Fields(reason)
	if len(fields) == 0 {
		return false
	}
	switch fields[0] {
	case "schema-drift", "expected":
		return true
	}
	return false
}

package doublecapture

import (
	"go/ast"
	"go/types"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

// doubleLane reports a capture call and a notify call that both run on one
// execution path through the err-branch body (D006 double-lane): error/warn
// already reach the user lane, so pairing a capture with a notify double-shows
// the user. It is path-sensitive - a notify in one if/else or switch/select leg
// and a capture in an exclusive leg do not pair, nor does a capture after a
// notify+return. if/else and the case legs of switch/type-switch/select are
// followed precisely (only one runs); loops stay opaque (a body may run with
// both legs across iterations - keep pairing there). Returns the first such
// (capture, notify) pairing, or (nil, nil).
func doubleLane(info *types.Info, body *ast.BlockStmt, errNames []string) (*ast.CallExpr, *ast.CallExpr) {
	ls := &laneScan{info: info, errNames: errNames}
	ls.walk(body.List, []laneState{{}})
	return ls.foundCap, ls.foundNotify
}

// laneState tracks, for one live path prefix, the first capture and first
// notify seen. Both non-nil means that path double-shows the user.
type laneState struct {
	capture *ast.CallExpr
	notify  *ast.CallExpr
}

type laneScan struct {
	info        *types.Info
	errNames    []string
	foundCap    *ast.CallExpr
	foundNotify *ast.CallExpr
}

// walk threads the incoming live states through a statement list, returning the
// live states that fall through to its end. Short-circuits once a pairing fires.
func (ls *laneScan) walk(stmts []ast.Stmt, in []laneState) []laneState {
	cur := in
	for _, st := range stmts {
		if ls.foundCap != nil {
			return []laneState{}
		}
		cur = ls.step(st, cur)
	}
	return cur
}

func (ls *laneScan) step(st ast.Stmt, in []laneState) []laneState {
	switch s := st.(type) {
	case *ast.BlockStmt:
		return ls.walk(s.List, in)
	case *ast.IfStmt:
		base := in
		if s.Init != nil {
			base = ls.mark(s.Init, base)
		}
		base = ls.mark(s.Cond, base)
		thenExit := ls.walk(s.Body.List, base)
		elseExit := base
		if s.Else != nil {
			elseExit = ls.step(s.Else, base)
		}
		return mergeLanes(thenExit, elseExit)
	case *ast.SwitchStmt:
		base := in
		if s.Init != nil {
			base = ls.mark(s.Init, base)
		}
		if s.Tag != nil {
			base = ls.mark(s.Tag, base)
		}
		return ls.caseLegs(s.Body.List, base)
	case *ast.TypeSwitchStmt:
		base := in
		if s.Init != nil {
			base = ls.mark(s.Init, base)
		}
		if s.Assign != nil {
			base = ls.mark(s.Assign, base)
		}
		return ls.caseLegs(s.Body.List, base)
	case *ast.SelectStmt:
		return ls.caseLegs(s.Body.List, in)
	case *ast.ReturnStmt:
		ls.mark(s, in)         // a capture/notify in the return expr still counts
		return []laneState{} // the path ends here
	case *ast.BranchStmt:
		return []laneState{} // break/continue/goto/fallthrough leave this straight-line path
	default:
		// ExprStmt, AssignStmt, DeclStmt, and the opaque loops (for / range):
		// flat-scan their direct calls (funclit bodies excluded) and mark. The
		// path continues.
		return ls.mark(s, in)
	}
}

// caseLegs walks the exclusive clauses of a switch / type-switch / select: only
// one runs, so each clause body is an exclusive leg from base and the exits
// union (like if/else). A missing default leaves a no-match path carrying base
// straight through. A clause's comm/init is marked into its own leg.
func (ls *laneScan) caseLegs(clauses []ast.Stmt, base []laneState) []laneState {
	var exits []laneState
	hasDefault := false
	for _, cl := range clauses {
		legBase, body := base, []ast.Stmt(nil)
		switch c := cl.(type) {
		case *ast.CaseClause:
			hasDefault = hasDefault || c.List == nil
			body = c.Body
		case *ast.CommClause:
			if c.Comm == nil {
				hasDefault = true
			} else {
				legBase = ls.mark(c.Comm, base)
			}
			body = c.Body
		default:
			continue
		}
		exits = append(exits, ls.walk(body, legBase)...)
	}
	if !hasDefault {
		exits = append(exits, base...) // no clause matched: base falls through
	}
	return dedupLanes(exits)
}

// mark scans node for the first capture and first notify call (funclit bodies
// excluded) and applies them to every live state, recording a pairing when a
// state now carries both.
func (ls *laneScan) mark(node ast.Node, in []laneState) []laneState {
	var capture, notify *ast.CallExpr
	ast.Inspect(node, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		if capture == nil && ls.isCapture(call) {
			capture = call
		}
		if notify == nil && ls.isNotify(call) {
			notify = call
		}
		return true
	})
	if capture == nil && notify == nil {
		return in
	}
	out := in[:0:0]
	for _, s := range in {
		if capture != nil && s.capture == nil {
			s.capture = capture
		}
		if notify != nil && s.notify == nil {
			s.notify = notify
		}
		if ls.foundCap == nil && s.capture != nil && s.notify != nil {
			ls.foundCap, ls.foundNotify = s.capture, s.notify
		}
		out = append(out, s)
	}
	return dedupLanes(out)
}

func (ls *laneScan) isCapture(call *ast.CallExpr) bool {
	for _, name := range ls.errNames {
		if astutil.IsCaptureErr(ls.info, call, name) {
			return true
		}
	}
	return false
}

func (ls *laneScan) isNotify(call *ast.CallExpr) bool {
	if !astutil.IsReportNotify(ls.info, call) {
		return false
	}
	for _, name := range ls.errNames {
		if astutil.ArgFlows(call, name) {
			return true
		}
	}
	return false
}

// mergeLanes unions two live-state sets (the fall-throughs of an if's two legs).
func mergeLanes(a, b []laneState) []laneState {
	return dedupLanes(append(append([]laneState{}, a...), b...))
}

// dedupLanes collapses states that agree on which lanes have fired - the two
// booleans are all that matter, so at most four states survive.
func dedupLanes(states []laneState) []laneState {
	var out []laneState
	seen := map[[2]bool]bool{}
	for _, s := range states {
		key := [2]bool{s.capture != nil, s.notify != nil}
		if seen[key] {
			continue
		}
		seen[key] = true
		out = append(out, s)
	}
	return out
}

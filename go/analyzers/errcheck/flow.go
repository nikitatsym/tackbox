package errcheck

import (
	"go/ast"
	"go/token"
	"go/types"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

// hasSilentPath reports whether some execution path through an err-branch body
// drops the checked error - reaches a plain return / break / continue / bare
// terminal exit, or the body's end, without an event routing the error on the
// way. aliases are the guarded error plus its errors.As bindings; a handle
// through any of them credits the guarded error.
func hasSilentPath(info *types.Info, body *ast.BlockStmt, aliases []string) bool {
	sc := &silentScan{info: info, body: body, aliases: aliases}
	end := sc.block(body.List, sBare)
	return sc.hit || end.bare // a bare fall-off-the-end drops the error too
}

// silentScan is a path-sensitive walk of an err-branch body. Each live path
// carries one bit - bare (no routing event yet) vs safe (an event has fired) -
// so the walk is linear, no path enumeration. if/else legs are followed
// precisely, so a handled leg never credits its silent complement; loops /
// switch / select stay opaque, lenient units (order-blind, matching the
// pre-path leniency there); goto/labels are conservative. Ported from the JS
// makeHandledAnalysis / Java SilentScan references.
type silentScan struct {
	info    *types.Info
	body    *ast.BlockStmt
	aliases []string
	hit     bool
}

// sstate: which kinds of path reach a program point. A capture/notify/death
// flips bare to safe; a propagating return or panic ends a path handled.
type sstate struct{ bare, safe bool }

var (
	sBare = sstate{bare: true}
	sDead = sstate{}
	sSafe = sstate{safe: true}
)

func (s sstate) dead() bool         { return !s.bare && !s.safe }
func (s sstate) or(o sstate) sstate { return sstate{s.bare || o.bare, s.safe || o.safe} }

func (sc *silentScan) block(stmts []ast.Stmt, in sstate) sstate {
	s := in
	for _, st := range stmts {
		s = sc.step(st, s)
	}
	return s
}

func (sc *silentScan) step(st ast.Stmt, in sstate) sstate {
	if sc.hit || in.dead() {
		return in
	}
	switch s := st.(type) {
	case *ast.BlockStmt:
		return sc.block(s.List, in)
	case *ast.IfStmt:
		c := in
		if s.Init != nil {
			c = sc.mark(s.Init, c)
		}
		c = sc.mark(s.Cond, c)
		then := sc.block(s.Body.List, c)
		other := c
		if s.Else != nil {
			other = sc.step(s.Else, c)
		}
		return then.or(other)
	case *ast.ReturnStmt:
		if sc.propagatesRet(s) {
			return sDead // the chain is carried onward: handled
		}
		sc.terminate(sc.mark(s, in)) // an event in the return expr still counts
		return sDead
	case *ast.ExprStmt:
		if call, ok := s.X.(*ast.CallExpr); ok && sc.isTerminalCall(call) {
			c := sc.mark(s, in) // a printing terminal carrying the error is safe
			if sc.isPanicWithErr(call) {
				return sDead // panic carrying the error propagates it
			}
			sc.terminate(c) // os.Exit / bare log.Fatal drop the error
			return sDead
		}
		return sc.mark(s, in)
	case *ast.BranchStmt:
		if s.Tok == token.BREAK || s.Tok == token.CONTINUE {
			sc.terminate(in) // exits the branch with the error unhandled
		}
		return sDead // goto/fallthrough stay conservative - no silent hit
	case *ast.LabeledStmt:
		return sc.step(s.Stmt, in)
	default:
		return sc.opaque(st, in)
	}
}

func (sc *silentScan) terminate(s sstate) {
	if s.bare {
		sc.hit = true
	}
}

// mark flips a bare path to safe when node contains an event that routes an
// aliased error: a capture, a gated notify, or a printing-terminal death.
func (sc *silentScan) mark(node ast.Node, in sstate) sstate {
	if sc.eventIn(node) {
		return sSafe
	}
	return in
}

// opaque credits a loop / switch / select order-blind: a handle anywhere inside
// (event, propagating return, or panic carrying the error) covers the paths
// through it; otherwise the incoming state passes through unchanged.
func (sc *silentScan) opaque(st ast.Stmt, in sstate) sstate {
	if sc.handledIn(st) {
		return sSafe
	}
	return in
}

func (sc *silentScan) propagatesRet(ret *ast.ReturnStmt) bool {
	for _, name := range sc.aliases {
		if astutil.ReturnPropagates(sc.info, sc.body, ret, name) {
			return true
		}
	}
	return false
}

// scanNonFunc walks root's subtree excluding nested func literals, calling
// visit until it returns false (a match found) or the subtree is exhausted.
func scanNonFunc(root ast.Node, visit func(ast.Node) bool) {
	stop := false
	ast.Inspect(root, func(n ast.Node) bool {
		if stop {
			return false
		}
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		if !visit(n) {
			stop = true
			return false
		}
		return true
	})
}

// eventIn reports whether node's subtree (nested func literals excluded)
// contains a call routing an aliased error to a sink.
func (sc *silentScan) eventIn(node ast.Node) bool {
	found := false
	scanNonFunc(node, func(n ast.Node) bool {
		if call, ok := n.(*ast.CallExpr); ok && sc.isEventCall(call) {
			found = true
			return false
		}
		return true
	})
	return found
}

func (sc *silentScan) isEventCall(call *ast.CallExpr) bool {
	for _, name := range sc.aliases {
		if astutil.IsCapture(sc.info, call, name) {
			return true
		}
	}
	if astutil.IsReportNotify(sc.info, call) && sc.argFlowsAny(call) {
		return true
	}
	return astutil.IsPrintingTerminal(sc.info, call) && sc.argFlowsAny(call)
}

// handledIn is the lenient opaque credit: an event, a propagating return, or a
// panic carrying an aliased error anywhere in node's subtree.
func (sc *silentScan) handledIn(node ast.Node) bool {
	found := false
	scanNonFunc(node, func(n ast.Node) bool {
		switch x := n.(type) {
		case *ast.CallExpr:
			if sc.isEventCall(x) || sc.isPanicWithErr(x) {
				found = true
				return false
			}
		case *ast.ReturnStmt:
			if sc.propagatesRet(x) {
				found = true
				return false
			}
		}
		return true
	})
	return found
}

func (sc *silentScan) argFlowsAny(call *ast.CallExpr) bool {
	for _, name := range sc.aliases {
		if astutil.ArgFlows(call, name) {
			return true
		}
	}
	return false
}

// isTerminalCall reports whether call never returns to the branch: panic,
// os.Exit, or a printing terminal (log.Fatal* / die).
func (sc *silentScan) isTerminalCall(call *ast.CallExpr) bool {
	return sc.isPanicCall(call) || sc.isOsExit(call) || astutil.IsPrintingTerminal(sc.info, call)
}

func (sc *silentScan) isPanicCall(call *ast.CallExpr) bool {
	id, ok := call.Fun.(*ast.Ident)
	return ok && id.Name == "panic"
}

func (sc *silentScan) isPanicWithErr(call *ast.CallExpr) bool {
	return sc.isPanicCall(call) && sc.argFlowsAny(call)
}

func (sc *silentScan) isOsExit(call *ast.CallExpr) bool {
	return astutil.QualifiedName(call.Fun) == "os.Exit"
}

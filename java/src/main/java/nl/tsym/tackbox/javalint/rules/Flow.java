package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.BreakStmt;
import com.github.javaparser.ast.stmt.ContinueStmt;
import com.github.javaparser.ast.stmt.ExpressionStmt;
import com.github.javaparser.ast.stmt.IfStmt;
import com.github.javaparser.ast.stmt.ReturnStmt;
import com.github.javaparser.ast.stmt.Statement;
import com.github.javaparser.ast.stmt.SynchronizedStmt;
import com.github.javaparser.ast.stmt.ThrowStmt;
import java.util.function.Predicate;
import nl.tsym.tackbox.javalint.MarkerIndex;

/** Path walk of a catch body: the spec's same-branch doctrine judges execution
 *  paths, not the flat statement bag, so a terminal guard (`if (...) throw ...;`)
 *  splits the body into exclusive legs. if/else and plain blocks are followed;
 *  loops, switches, and nested trys stay opaque Frame-scanned units (their
 *  internal order is not modeled, preserving the pre-path behavior there), and
 *  nested scopes are skipped by Frame as ever. The state space is two booleans
 *  per walk, so the walk is linear - no path enumeration. */
final class Flow {

    private Flow() {}

    /** A double capture found on one execution path: where the caught was
     *  reported and where it then rethrows. Null when no path does both. */
    record Double(int reportLine, int rethrowLine) {}

    /** JV006: some execution path captures the caught and then reaches a throw
     *  that propagates it - report-then-rethrow on one path. Exclusive legs
     *  (a capture after a terminal guard's throw) do not pair. */
    static Double doubleCapture(BlockStmt body, Predicate<MethodCallExpr> captures,
            Predicate<ThrowStmt> propagates) {
        DoubleScan scan = new DoubleScan(captures, propagates);
        scan.block(body, DoubleScan.LIVE);
        return scan.found;
    }

    /** JV001: some execution path terminates (return, break, continue, or the
     *  body's end) without a report on the way and without marker cover; the
     *  result is the line that path ends on, or -1 when every path is covered.
     *  A throw is never a silent termination. Marker cover is per-statement: a
     *  `// no-report:` block above a statement covers every path executing it,
     *  which subsumes the whole-catch placement above the first statement. */
    static int silentPathEnd(BlockStmt body, Predicate<MethodCallExpr> reports,
            MarkerIndex markers) {
        SilentScan scan = new SilentScan(reports, markers);
        SilentScan.St out = scan.block(body, SilentScan.BARE);
        if (scan.hitLine >= 0) {
            return scan.hitLine;
        }
        return out.bare() ? body.getEnd().orElseThrow().line : -1;
    }

    /** JV006 walk. State: does a clean path reach here, does a captured one
     *  (and where its first capture ran). Captures inside opaque units and
     *  conditions count as may-have-run - conservative toward flagging, the
     *  pre-path rule's direction. */
    private static final class DoubleScan {

        /** (clean path alive, captured path alive); captureLine is the first
         *  capture on a captured path, meaningful only when captured. */
        private record St(boolean clean, boolean captured, int captureLine) {
            boolean dead() {
                return !clean && !captured;
            }

            St or(St o) {
                int line = !captured ? o.captureLine
                        : !o.captured ? captureLine : Math.min(captureLine, o.captureLine);
                return new St(clean || o.clean, captured || o.captured, line);
            }
        }

        static final St LIVE = new St(true, false, -1);
        private static final St DEAD = new St(false, false, -1);

        private final Predicate<MethodCallExpr> captures;
        private final Predicate<ThrowStmt> propagates;
        Double found;

        DoubleScan(Predicate<MethodCallExpr> captures, Predicate<ThrowStmt> propagates) {
            this.captures = captures;
            this.propagates = propagates;
        }

        St block(BlockStmt b, St in) {
            St s = in;
            for (Statement st : b.getStatements()) {
                s = step(st, s);
            }
            return s;
        }

        private St step(Statement st, St in) {
            // dup-ok: parallel state walkers kept separate on purpose; generic merge couples them
            if (found != null || in.dead()) {
                return in;
            }
            if (st instanceof BlockStmt b) {
                return block(b, in);
            }
            if (st instanceof IfStmt f) {
                St c = mark(f.getCondition(), in);
                St then = step(f.getThenStmt(), c);
                St other = f.getElseStmt().map(e -> step(e, c)).orElse(c);
                return then.or(other);
            }
            if (st instanceof ThrowStmt t) {
                St c = mark(t.getExpression(), in);
                if (c.captured() && propagates.test(t)) {
                    found = new Double(c.captureLine(), line(t));
                }
                return DEAD;
            }
            if (st instanceof ReturnStmt || st instanceof BreakStmt
                    || st instanceof ContinueStmt) {
                // dup-ok: parallel state walkers kept separate on purpose; generic merge couples them
                // (DoubleScan leg; the SilentScan twin sits below)
                return DEAD;
            }
            if (st instanceof ExpressionStmt e) {
                return mark(e.getExpression(), in);
            }
            if (st instanceof SynchronizedStmt sy) {
                return block(sy.getBody(), mark(sy.getExpression(), in));
            }
            return opaque(st, in);
        }

        /** An unconditionally-evaluated expression: a capture in it runs on
         *  every path through this point. */
        private St mark(Node expr, St in) {
            MethodCallExpr cap = firstCapture(expr);
            return cap == null ? in : new St(false, true, captureLine(in, cap));
        }

        /** A loop / switch / nested try: order-blind inside, exactly the
         *  pre-path semantics. A capture may have run (both states stay
         *  alive); a propagating throw pairs with any capture at hand. */
        private St opaque(Statement st, St in) {
            Frame f = Frame.scan(st);
            MethodCallExpr cap = f.calls.stream().filter(captures).findFirst().orElse(null);
            if (found == null && (cap != null || in.captured())) {
                f.throwsStmts.stream().filter(propagates).findFirst().ifPresent(t -> {
                    int reported = cap != null && in.captureLine() < 0 ? line(cap) : in.captureLine();
                    found = new Double(reported, line(t));
                });
            }
            return cap == null ? in : new St(in.clean(), true, captureLine(in, cap));
        }

        /** Keep the earliest capture already on a captured path; a fresh
         *  capture on a so-far-clean path contributes its own line. */
        private static int captureLine(St in, MethodCallExpr cap) {
            return in.captured() ? in.captureLine() : line(cap);
        }

        private MethodCallExpr firstCapture(Node n) {
            return Frame.scan(n).calls.stream().filter(captures).findFirst().orElse(null);
        }

        private static int line(Node n) {
            return n.getBegin().orElseThrow().line;
        }
    }

    /** JV001 walk. State: does a bare (unreported, uncovered) path reach here.
     *  Opaque units keep the pre-path leniency: a throw or report anywhere
     *  inside covers the paths passing through them. */
    private static final class SilentScan {

        /** (bare path alive, safe path alive). */
        record St(boolean bare, boolean safe) {
            boolean dead() {
                return !bare && !safe;
            }

            St or(St o) {
                return new St(bare || o.bare, safe || o.safe);
            }
        }

        static final St BARE = new St(true, false);
        private static final St DEAD = new St(false, false);
        private static final St SAFE = new St(false, true);

        private final Predicate<MethodCallExpr> reports;
        private final MarkerIndex markers;
        int hitLine = -1;

        SilentScan(Predicate<MethodCallExpr> reports, MarkerIndex markers) {
            this.reports = reports;
            this.markers = markers;
        }

        St block(BlockStmt b, St in) {
            St s = in;
            for (Statement st : b.getStatements()) {
                if (s.bare() && Markers.noReportAbove(markers, st)) {
                    s = new St(false, true);
                }
                s = step(st, s);
            }
            return s;
        }

        private St step(Statement st, St in) {
            // dup-ok: parallel state walkers kept separate on purpose; generic merge couples them
            if (hitLine >= 0 || in.dead()) {
                return in;
            }
            if (st instanceof BlockStmt b) {
                return block(b, in);
            }
            if (st instanceof IfStmt f) {
                St c = mark(f.getCondition(), in);
                St then = step(f.getThenStmt(), c);
                St other = f.getElseStmt().map(e -> step(e, c)).orElse(c);
                return then.or(other);
            }
            if (st instanceof ThrowStmt) {
                return DEAD;
            }
            if (st instanceof ReturnStmt r) {
                St c = r.getExpression().map(e -> mark(e, in)).orElse(in);
                terminate(c, st);
                return DEAD;
            }
            if (st instanceof BreakStmt || st instanceof ContinueStmt) {
                terminate(in, st);
                // dup-ok: parallel state walkers kept separate on purpose; generic merge couples them
                // (SilentScan leg; the DoubleScan twin sits above)
                return DEAD;
            }
            if (st instanceof ExpressionStmt e) {
                return mark(e.getExpression(), in);
            }
            if (st instanceof SynchronizedStmt sy) {
                return block(sy.getBody(), mark(sy.getExpression(), in));
            }
            return opaque(st, in);
        }

        private void terminate(St s, Statement at) {
            if (s.bare() && hitLine < 0) {
                hitLine = at.getBegin().orElseThrow().line;
            }
        }

        private St mark(Node expr, St in) {
            return reportsIn(expr) ? SAFE : in;
        }

        private St opaque(Statement st, St in) {
            Frame f = Frame.scan(st);
            return f.hasThrow || f.calls.stream().anyMatch(reports) ? SAFE : in;
        }

        private boolean reportsIn(Node n) {
            return Frame.scan(n).calls.stream().anyMatch(reports);
        }
    }
}

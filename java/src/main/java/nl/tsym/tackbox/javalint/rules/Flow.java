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

    /** JV006: some execution path captures the caught and then reaches a throw
     *  that propagates it - report-then-rethrow on one path. Exclusive legs
     *  (a capture after a terminal guard's throw) do not pair. */
    static boolean doubleCaptures(BlockStmt body, Predicate<MethodCallExpr> captures,
            Predicate<ThrowStmt> propagates) {
        DoubleScan scan = new DoubleScan(captures, propagates);
        scan.block(body, DoubleScan.LIVE);
        return scan.hit;
    }

    /** JV001: some execution path terminates (return, break, continue, or the
     *  body's end) without a report on the way and without marker cover. A
     *  throw is never a silent termination. Marker cover is per-statement: a
     *  `// no-report:` block above a statement covers every path executing it,
     *  which subsumes the whole-catch placement above the first statement. */
    static boolean hasSilentPath(BlockStmt body, Predicate<MethodCallExpr> reports,
            MarkerIndex markers) {
        SilentScan scan = new SilentScan(reports, markers);
        SilentScan.St out = scan.block(body, SilentScan.BARE);
        return scan.hit || out.bare();
    }

    /** JV006 walk. State: does a clean path reach here, does a captured one.
     *  Captures inside opaque units and conditions count as may-have-run -
     *  conservative toward flagging, the pre-path rule's direction. */
    private static final class DoubleScan {

        /** (clean path alive, captured path alive) packed as an enum lattice. */
        private record St(boolean clean, boolean captured) {
            boolean dead() {
                return !clean && !captured;
            }

            St or(St o) {
                return new St(clean || o.clean, captured || o.captured);
            }
        }

        static final St LIVE = new St(true, false);
        private static final St DEAD = new St(false, false);

        private final Predicate<MethodCallExpr> captures;
        private final Predicate<ThrowStmt> propagates;
        boolean hit;

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
            if (hit || in.dead()) {
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
                    hit = true;
                }
                return DEAD;
            }
            if (st instanceof ReturnStmt || st instanceof BreakStmt
                    || st instanceof ContinueStmt) {
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
            return capturesIn(expr) ? new St(false, true) : in;
        }

        /** A loop / switch / nested try: order-blind inside, exactly the
         *  pre-path semantics. A capture may have run (both states stay
         *  alive); a propagating throw pairs with any capture at hand. */
        private St opaque(Statement st, St in) {
            Frame f = Frame.scan(st);
            boolean cap = f.calls.stream().anyMatch(captures);
            if ((cap || in.captured()) && f.throwsStmts.stream().anyMatch(propagates)) {
                hit = true;
            }
            return cap ? new St(in.clean(), true) : in;
        }

        private boolean capturesIn(Node n) {
            return Frame.scan(n).calls.stream().anyMatch(captures);
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
        boolean hit;

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
            if (hit || in.dead()) {
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
                terminate(c);
                return DEAD;
            }
            if (st instanceof BreakStmt || st instanceof ContinueStmt) {
                terminate(in);
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

        private void terminate(St s) {
            if (s.bare()) {
                hit = true;
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

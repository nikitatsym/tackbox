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
import com.github.javaparser.ast.stmt.SwitchEntry;
import com.github.javaparser.ast.stmt.SwitchStmt;
import com.github.javaparser.ast.stmt.SynchronizedStmt;
import com.github.javaparser.ast.stmt.ThrowStmt;
import java.util.ArrayList;
import java.util.List;
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

    /** A double-lane found on one execution path: where the caught was captured
     *  and where it was notified. Null when no path does both. */
    record Lane(int captureLine, int notifyLine) {}

    private static int line(Node n) {
        return n.getBegin().orElseThrow().line;
    }

    /** JV006: some execution path captures the caught and then reaches a throw
     *  that propagates it - report-then-rethrow on one path. Exclusive legs
     *  (a capture after a terminal guard's throw) do not pair. */
    static Double doubleCapture(BlockStmt body, Predicate<MethodCallExpr> captures,
            Predicate<ThrowStmt> propagates) {
        DoubleScan scan = new DoubleScan(captures, propagates);
        scan.block(body, DoubleScan.LIVE);
        return scan.found;
    }

    /** JV006 double-lane (D006): some execution path both captures the caught
     *  and notifies with it - error/warn already reach the user lane, so the
     *  notify double-shows. Path-correlated (unlike DoubleScan's boolean merge):
     *  exclusive if/else legs must not pair, since a lane conflict needs both on
     *  ONE path, so a set of live states is threaded. if/else and switch entries
     *  are followed (only one runs); loops / nested try stay opaque Frame-scanned
     *  units (may-run). */
    static Lane laneConflict(BlockStmt body, Predicate<MethodCallExpr> captures,
            Predicate<MethodCallExpr> notifies) {
        LaneScan scan = new LaneScan(captures, notifies);
        scan.walk(body.getStatements(), List.of(LaneScan.EMPTY));
        return scan.found;
    }

    /** JV001: some execution path terminates (return, break, continue, or the
     *  body's end) without a report on the way and without marker cover; the
     *  result is the line that path ends on, or -1 when every path is covered.
     *  A throw is never a silent termination. Marker cover is per-statement: a
     *  no-report block above a statement covers every path executing it,
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

    /** JV006 double-lane walk. Each live path carries the line of its first
     *  capture and first notify (-1 = unseen); a state with both fires. if/else
     *  legs and switch entries are threaded as separate states so exclusive legs
     *  never pair; a terminator-less colon-case falls through into the next. */
    private static final class LaneScan {

        record St(int captureLine, int notifyLine) {}

        static final St EMPTY = new St(-1, -1);

        private final Predicate<MethodCallExpr> captures;
        private final Predicate<MethodCallExpr> notifies;
        Lane found;

        LaneScan(Predicate<MethodCallExpr> captures, Predicate<MethodCallExpr> notifies) {
            this.captures = captures;
            this.notifies = notifies;
        }

        List<St> walk(List<Statement> stmts, List<St> in) {
            List<St> cur = in;
            for (Statement st : stmts) {
                if (found != null) {
                    return List.of();
                }
                cur = step(st, cur);
            }
            return cur;
        }

        private List<St> step(Statement st, List<St> in) {
            if (st instanceof BlockStmt b) {
                return walk(b.getStatements(), in);
            }
            if (st instanceof IfStmt f) {
                List<St> base = mark(f.getCondition(), in);
                List<St> then = step(f.getThenStmt(), base);
                List<St> other = f.getElseStmt().map(e -> step(e, base)).orElse(base);
                return merge(then, other);
            }
            if (st instanceof SwitchStmt sw) {
                return switchEntries(sw, in);
            }
            if (st instanceof ReturnStmt || st instanceof ThrowStmt
                    || st instanceof BreakStmt || st instanceof ContinueStmt) {
                mark(st, in); // a capture/notify in the returned/thrown expr still counts
                return List.of();
            }
            return mark(st, in);
        }

        /** Switch entries are exclusive legs (only one runs), but a colon-form
         *  case with no terminator falls into the next: thread each entry's
         *  fall-through exit into the next entry's start, so only a real
         *  fall-through pairs. A missing default leaves a no-match path. */
        private List<St> switchEntries(SwitchStmt sw, List<St> in) {
            List<St> base = mark(sw.getSelector(), in);
            List<St> fall = List.of();
            boolean sawDefault = false;
            for (SwitchEntry entry : sw.getEntries()) {
                sawDefault |= entry.getLabels().isEmpty();
                List<St> exit = walk(entry.getStatements(), merge(base, fall));
                fall = entry.getType() == SwitchEntry.Type.STATEMENT_GROUP ? exit : List.of();
            }
            return sawDefault ? fall : merge(fall, base);
        }

        /** Frame-scan node for its first capture and first notify (nested scopes
         *  skipped, compound units flattened), folding them into every live
         *  state; a state that now carries both fires. */
        private List<St> mark(Node node, List<St> in) {
            Frame f = Frame.scan(node);
            int capLine = f.calls.stream().filter(captures).findFirst().map(Flow::line).orElse(-1);
            int notifyLine = f.calls.stream().filter(notifies).findFirst().map(Flow::line).orElse(-1);
            if (capLine < 0 && notifyLine < 0) {
                return in;
            }
            List<St> out = new ArrayList<>();
            for (St s : in) {
                int nc = s.captureLine() >= 0 ? s.captureLine() : capLine;
                int nn = s.notifyLine() >= 0 ? s.notifyLine() : notifyLine;
                if (found == null && nc >= 0 && nn >= 0) {
                    found = new Lane(nc, nn);
                }
                St ns = new St(nc, nn);
                if (!out.contains(ns)) {
                    out.add(ns);
                }
            }
            return out;
        }

        private static List<St> merge(List<St> a, List<St> b) {
            List<St> out = new ArrayList<>(a);
            for (St s : b) {
                if (!out.contains(s)) {
                    out.add(s);
                }
            }
            return out;
        }
    }
}

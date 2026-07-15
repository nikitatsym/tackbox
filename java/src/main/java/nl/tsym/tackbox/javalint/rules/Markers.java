package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.CatchClause;
import com.github.javaparser.ast.stmt.Statement;
import nl.tsym.tackbox.javalint.Marker;
import nl.tsym.tackbox.javalint.MarkerIndex;

/** Shared `// no-report:` lookups: the block-above convention (a marker on the
 *  comment block directly above the anchor) applied to a catch clause and to a
 *  single statement. The anchor for a catch is its first body statement, or the
 *  catch clause itself when the body is empty. A dead marker near a firing
 *  finding becomes a message hint, so the author learns why it did not count
 *  instead of guessing. */
final class Markers {

    private Markers() {}

    /** Hint for a `no-report:` near the catch that suppresses nothing: dead
     *  (trailing `try {`, the catch line, or a body statement; empty-reason in
     *  the body), or live but standalone above the `try`, where no catch anchor
     *  ever looks. Empty when none. */
    static String deadNoReportHint(MarkerIndex idx, CatchClause cc) {
        int from = cc.getParentNode().flatMap(p -> p.getBegin()).map(b -> b.line)
                .orElse(cc.getBegin().orElseThrow().line);
        int to = cc.getEnd().orElseThrow().line;
        String hint = hint(idx, from, to);
        if (!hint.isEmpty()) {
            return hint;
        }
        Marker aboveTry = idx.above(from);
        if (aboveTry != null && aboveTry.kind() == Marker.Kind.NO_REPORT) {
            return " (a no-report above `try` does not cover its catches - place it"
                    + " above the catch's first body statement)";
        }
        return "";
    }

    /** Hint for a dead `no-report:` on the statement's own line (trailing) or
     *  the line directly above it (where a live marker would sit). */
    static String deadNoReportHint(MarkerIndex idx, Statement stmt) {
        int line = stmt.getBegin().orElseThrow().line;
        return hint(idx, line - 1, line);
    }

    private static String hint(MarkerIndex idx, int from, int to) {
        for (MarkerIndex.Dead d : idx.dead()) {
            if (d.kind() == Marker.Kind.NO_REPORT && d.line() >= from && d.line() <= to) {
                return " (the no-report on line " + d.line() + " is ignored: "
                        + (d.cause() == MarkerIndex.Cause.TRAILING
                                ? "it trails code - a marker is a standalone comment line"
                                        + " above the statement it covers"
                                : "its reason is under 10 characters - a marker needs a reason"
                                        + " of at least 10 characters")
                        + ")";
            }
        }
        return "";
    }

    static boolean noReportAbove(MarkerIndex idx, CatchClause cc) {
        BlockStmt body = cc.getBody();
        int line = body.getStatements().isEmpty()
                ? cc.getBegin().orElseThrow().line
                : body.getStatement(0).getBegin().orElseThrow().line;
        return isNoReport(idx, line);
    }

    static boolean noReportAbove(MarkerIndex idx, Statement stmt) {
        return isNoReport(idx, stmt.getBegin().orElseThrow().line);
    }

    private static boolean isNoReport(MarkerIndex idx, int line) {
        Marker m = idx.above(line);
        return m != null && m.kind() == Marker.Kind.NO_REPORT;
    }
}

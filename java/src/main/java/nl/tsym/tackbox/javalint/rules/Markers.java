package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.CatchClause;
import com.github.javaparser.ast.stmt.Statement;
import nl.tsym.tackbox.javalint.Marker;
import nl.tsym.tackbox.javalint.MarkerIndex;

/** Shared `// no-report:` lookups: the block-above convention (a marker on the
 *  comment block directly above the anchor) applied to a catch clause and to a
 *  single statement. The anchor for a catch is its first body statement, or the
 *  catch clause itself when the body is empty. */
final class Markers {

    private Markers() {}

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

package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.stmt.CatchClause;
import com.github.javaparser.ast.stmt.ExpressionStmt;
import java.util.ArrayList;
import java.util.List;
import nl.tsym.tackbox.javalint.Finding;
import nl.tsym.tackbox.javalint.MarkerIndex;
import nl.tsym.tackbox.javalint.Recognition;

/** JV005 (exit): System.exit inside a catch must be preceded, in the same frame,
 *  by a report or a print of the caught, or carry a `// no-report: <reason>`
 *  marker. Otherwise the process dies on a swallowed error. Ordering matters -
 *  a report after the exit never runs on the exiting path (port of ERC003). */
public final class ExitRule {

    public static final String ID = "JV005";

    private static final String MESSAGE =
            ID + ": System.exit in catch must be preceded by a report or print of the"
            + " caught, or carry `// no-report: <reason>`";

    private final Recognition rec;

    public ExitRule(Recognition rec) {
        this.rec = rec;
    }

    public List<Finding> check(String file, CompilationUnit cu, MarkerIndex markers) {
        List<Finding> out = new ArrayList<>();
        for (CatchClause cc : cu.findAll(CatchClause.class)) {
            String caught = cc.getParameter().getNameAsString();
            Frame f = Frame.scan(cc.getBody());
            for (int i = 0; i < f.calls.size(); i++) {
                MethodCallExpr call = f.calls.get(i);
                if (!isSystemExit(call) || markerAboveExit(markers, call)
                        || coveredBefore(cu, f.calls, i, caught)) {
                    continue;
                }
                Position p = call.getBegin().orElseThrow();
                String hint = call.findAncestor(ExpressionStmt.class)
                        .map(st -> Markers.deadNoReportHint(markers, st))
                        .orElse("");
                out.add(new Finding(ID, file, p.line, p.column, p.line, p.column, MESSAGE + hint));
            }
        }
        return out;
    }

    private boolean coveredBefore(CompilationUnit cu, List<MethodCallExpr> calls, int exit, String caught) {
        for (int j = 0; j < exit; j++) {
            if (rec.capturesOrPrints(cu, calls.get(j), caught)) {
                return true;
            }
        }
        return false;
    }

    private static boolean isSystemExit(MethodCallExpr call) {
        Expression scope = call.getScope().orElse(null);
        return call.getNameAsString().equals("exit")
                && scope instanceof NameExpr ne && ne.getNameAsString().equals("System");
    }

    private static boolean markerAboveExit(MarkerIndex markers, MethodCallExpr call) {
        return call.findAncestor(ExpressionStmt.class)
                .map(st -> Markers.noReportAbove(markers, st))
                .orElse(false);
    }
}

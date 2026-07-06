package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.stmt.CatchClause;
import java.util.ArrayList;
import java.util.List;
import nl.tsym.tackbox.javalint.Finding;
import nl.tsym.tackbox.javalint.MarkerIndex;
import nl.tsym.tackbox.javalint.Recognition;

/** JV001 (swallow): a catch must propagate the exception (a `throw` in its own
 *  frame), report it (a tier-1/tier-2 capture receiving the caught), print it
 *  (a printing terminal), or carry a `// no-report: <reason>` marker. A catch
 *  that does none of these silently drops the error. */
public final class SwallowRule {

    public static final String ID = "JV001";

    private static final String MESSAGE =
            ID + ": catch swallows the exception; propagate it with `throw`, report or"
            + " print the caught, or carry `// no-report: <reason>`";

    private final Recognition rec;

    public SwallowRule(Recognition rec) {
        this.rec = rec;
    }

    public List<Finding> check(String file, CompilationUnit cu, MarkerIndex markers) {
        List<Finding> out = new ArrayList<>();
        for (CatchClause cc : cu.findAll(CatchClause.class)) {
            if (clean(cu, cc, markers)) {
                continue;
            }
            Position p = cc.getBegin().orElseThrow();
            out.add(new Finding(ID, file, p.line, p.column, p.line, p.column, MESSAGE));
        }
        return out;
    }

    private boolean clean(CompilationUnit cu, CatchClause cc, MarkerIndex markers) {
        Frame f = Frame.scan(cc.getBody());
        if (f.hasThrow || Markers.noReportAbove(markers, cc)) {
            return true;
        }
        String caught = cc.getParameter().getNameAsString();
        for (MethodCallExpr call : f.calls) {
            if (rec.capturesOrPrints(cu, call, caught)) {
                return true;
            }
        }
        return false;
    }
}

package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.stmt.CatchClause;
import com.github.javaparser.ast.stmt.ThrowStmt;
import java.util.ArrayList;
import java.util.List;
import nl.tsym.tackbox.javalint.Finding;
import nl.tsym.tackbox.javalint.Recognition;

/** JV006 (double capture, port of ERC005): a catch must not both report the caught
 *  (a tier-1 / tier-2 capture) and rethrow it. The upstream handler reports the
 *  propagated exception a second time, inflating the error backend - pick one:
 *  report and swallow, or propagate without reporting. The propagation leg only
 *  needs the caught to reach the throw (even stringified: a wrapped exception
 *  still reaches upstream carrying the failure), the way ERC005 gates on a return
 *  that references the err at all. A printing terminal is not a backend capture. */
public final class DoubleCaptureRule {

    public static final String ID = "JV006";

    private static final String MESSAGE =
            ID + ": catch both reports the caught and rethrows it; the upstream handler reports"
            + " it again (report and swallow, or propagate without reporting - not both)";

    private final Recognition rec;

    public DoubleCaptureRule(Recognition rec) {
        this.rec = rec;
    }

    public List<Finding> check(String file, CompilationUnit cu) {
        List<Finding> out = new ArrayList<>();
        for (CatchClause cc : cu.findAll(CatchClause.class)) {
            String caught = cc.getParameter().getNameAsString();
            Frame f = Frame.scan(cc.getBody());
            if (!captures(cu, f, caught) || !propagates(f, caught)) {
                continue;
            }
            Position p = cc.getBegin().orElseThrow();
            out.add(new Finding(ID, file, p.line, p.column, p.line, p.column, MESSAGE));
        }
        return out;
    }

    private boolean captures(CompilationUnit cu, Frame f, String caught) {
        for (MethodCallExpr call : f.calls) {
            if (rec.captures(cu, call, caught)) {
                return true;
            }
        }
        return false;
    }

    /** The caught reaches a throw in this frame - as the object or wrapped, and
     *  even stringified: any propagated exception reaches the upstream handler. */
    private static boolean propagates(Frame f, String caught) {
        for (ThrowStmt ts : f.throwsStmts) {
            if (ts.getExpression().findFirst(NameExpr.class,
                    ne -> ne.getNameAsString().equals(caught)).isPresent()) {
                return true;
            }
        }
        return false;
    }
}

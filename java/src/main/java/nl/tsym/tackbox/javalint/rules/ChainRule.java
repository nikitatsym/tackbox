package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.stmt.CatchClause;
import com.github.javaparser.ast.stmt.ThrowStmt;
import java.util.ArrayList;
import java.util.List;
import nl.tsym.tackbox.javalint.Finding;

/** JV002 (chain): a new exception thrown in a catch must carry the caught as its
 *  cause. Preservation is object flow - the caught reaches the new exception as a
 *  constructor argument, or is attached with initCause / addSuppressed. The chain
 *  breaks, and JV002 fires, only when the caught reaches the new exception solely
 *  through a string (getMessage / toString / string concatenation), or not at all.
 *  Rethrowing the caught itself (`throw e`) is not this rule - it is object flow.
 *  Orthogonal to JV001: a throw makes the catch non-silent, this checks the chain. */
public final class ChainRule {

    public static final String ID = "JV002";

    private static final String MESSAGE =
            ID + ": new exception thrown in catch drops the caught cause; pass the caught"
            + " as the cause (throw new X(msg, e), initCause, or addSuppressed)";

    public List<Finding> check(String file, CompilationUnit cu) {
        List<Finding> out = new ArrayList<>();
        for (CatchClause cc : cu.findAll(CatchClause.class)) {
            String caught = cc.getParameter().getNameAsString();
            Frame f = Frame.scan(cc.getBody());
            for (ThrowStmt ts : f.throwsStmts) {
                if (!dropsCause(f, ts, caught)) {
                    continue;
                }
                Position p = ts.getBegin().orElseThrow();
                out.add(new Finding(ID, file, p.line, p.column, p.line, p.column, MESSAGE));
            }
        }
        return out;
    }

    /** Whether this throw constructs a new exception we can see and that new
     *  exception drops the caught cause. A bare rethrow, or a throw of a value we
     *  cannot resolve to a `new` (a factory call, an inherited field), fails open. */
    private boolean dropsCause(Frame f, ThrowStmt ts, String caught) {
        Expression thrown = ts.getExpression();
        if (thrown instanceof ObjectCreationExpr) {
            return !ObjectFlow.flows(thrown, caught);
        }
        if (thrown instanceof NameExpr ne) {
            ObjectCreationExpr init = f.localNews.get(ne.getNameAsString());
            return init != null
                    && !ObjectFlow.flows(init, caught)
                    && !attachesCause(f, ne.getNameAsString(), caught);
        }
        return false;
    }

    /** A two-step attach: `local.initCause(e)` / `local.addSuppressed(e)` in the
     *  same frame, feeding the caught object to the exception the throw names. */
    private boolean attachesCause(Frame f, String local, String caught) {
        for (MethodCallExpr call : f.calls) {
            String m = call.getNameAsString();
            boolean onLocal = call.getScope()
                    .filter(s -> s instanceof NameExpr ne && ne.getNameAsString().equals(local)).isPresent();
            if (!onLocal || (!m.equals("initCause") && !m.equals("addSuppressed"))) {
                continue;
            }
            for (Expression arg : call.getArguments()) {
                if (ObjectFlow.flows(arg, caught)) {
                    return true;
                }
            }
        }
        return false;
    }
}

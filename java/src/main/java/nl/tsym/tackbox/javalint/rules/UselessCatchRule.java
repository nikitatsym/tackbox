package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.stmt.CatchClause;
import com.github.javaparser.ast.stmt.Statement;
import com.github.javaparser.ast.stmt.ThrowStmt;
import java.util.ArrayList;
import java.util.List;
import nl.tsym.tackbox.javalint.Finding;

/** JV004 (useless catch): a catch whose entire body is `throw <caught>;` is a
 *  no-op wrapper - it catches only to rethrow the same exception unchanged.
 *  Removing the try/catch lets the exception propagate identically. Port of
 *  opengrep java-useless-catch; a structural redundancy, not a swallow, so no
 *  `// no-report:` escape - the fix is deletion, not annotation. */
public final class UselessCatchRule {

    public static final String ID = "JV004";

    private static final String MESSAGE =
            ID + ": catch only rethrows the caught unchanged (`throw <caught>;`); remove the"
            + " try/catch and let it propagate";

    public List<Finding> check(String file, CompilationUnit cu) {
        List<Finding> out = new ArrayList<>();
        for (CatchClause cc : cu.findAll(CatchClause.class)) {
            if (!onlyRethrows(cc)) {
                continue;
            }
            Position p = cc.getBegin().orElseThrow();
            out.add(new Finding(ID, file, p.line, p.column, p.line, p.column, MESSAGE));
        }
        return out;
    }

    private static boolean onlyRethrows(CatchClause cc) {
        List<Statement> stmts = cc.getBody().getStatements();
        if (stmts.size() != 1 || !(stmts.get(0) instanceof ThrowStmt ts)) {
            return false;
        }
        return ts.getExpression() instanceof NameExpr ne
                && ne.getNameAsString().equals(cc.getParameter().getNameAsString());
    }
}

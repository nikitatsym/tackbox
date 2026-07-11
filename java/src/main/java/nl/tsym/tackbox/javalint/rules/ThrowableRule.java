package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.stmt.CatchClause;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.ast.type.ReferenceType;
import com.github.javaparser.ast.type.Type;
import com.github.javaparser.ast.type.UnionType;
import java.util.ArrayList;
import java.util.List;
import nl.tsym.tackbox.javalint.Finding;
import nl.tsym.tackbox.javalint.MarkerIndex;

/** JV003 (throwable): a catch of Throwable or Error must rethrow or carry a
 *  `// no-report: <reason>` marker. Reporting is not enough - VirtualMachineError
 *  and the other unrecoverable Errors must never be swallowed, only re-raised.
 *  Orthogonal to JV001: a captured Throwable is clean for JV001 but still JV003. */
public final class ThrowableRule {

    public static final String ID = "JV003";

    private static final String MESSAGE =
            ID + ": catch of Throwable/Error must rethrow"
            + " (an unrecoverable Error must never be swallowed)";

    public List<Finding> check(String file, CompilationUnit cu, MarkerIndex markers) {
        List<Finding> out = new ArrayList<>();
        for (CatchClause cc : cu.findAll(CatchClause.class)) {
            if (!catchesThrowableOrError(cc)) {
                continue;
            }
            Frame f = Frame.scan(cc.getBody());
            if (f.hasThrow || Markers.noReportAbove(markers, cc)) {
                continue;
            }
            Position p = cc.getBegin().orElseThrow();
            out.add(new Finding(ID, file, p.line, p.column, p.line, p.column,
                    MESSAGE + Markers.deadNoReportHint(markers, cc)));
        }
        return out;
    }

    private static boolean catchesThrowableOrError(CatchClause cc) {
        Type t = cc.getParameter().getType();
        if (t instanceof UnionType u) {
            for (ReferenceType part : u.getElements()) {
                if (isThrowableOrError(part)) {
                    return true;
                }
            }
            return false;
        }
        return isThrowableOrError(t);
    }

    private static boolean isThrowableOrError(Type t) {
        if (t instanceof ClassOrInterfaceType cit) {
            String n = cit.getNameAsString();
            return n.equals("Throwable") || n.equals("Error");
        }
        return false;
    }
}

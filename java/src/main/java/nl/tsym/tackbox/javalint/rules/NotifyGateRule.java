package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.stmt.CatchClause;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.ast.type.ReferenceType;
import com.github.javaparser.ast.type.Type;
import com.github.javaparser.ast.type.UnionType;
import java.util.List;
import java.util.Set;
import nl.tsym.tackbox.javalint.Finding;
import nl.tsym.tackbox.javalint.MarkerIndex;
import nl.tsym.tackbox.javalint.Recognition;

/** JV009 (notify gate, D006): a Report.notify carrying the caught terminates a
 *  failure path only when narrowed. Java has typed catches, so the narrowing
 *  gate is the catch type - a notify in a broad catch (Exception /
 *  RuntimeException / Throwable / Error, or a multi-catch with any broad member)
 *  routes every failure to the user lane and blinds the telemetry the operator
 *  watches. Gate strength is proportional to observability loss, and notify
 *  drops the only channel the operator sees (D006). The complement of a narrowed
 *  notify stays covered by JV001; a `// no-report:` marker is the last resort
 *  and a new one needs user approval. */
public final class NotifyGateRule extends CatchRule {

    public static final String ID = "JV009";

    private static final Set<String> BROAD =
            Set.of("Exception", "RuntimeException", "Throwable", "Error");

    private static final String MESSAGE =
            ID + ": notify in a broad catch routes every failure to the user lane and blinds"
            + " telemetry; catch a narrow exception type, or use Report.error/Report.warn;"
            + " a new // no-report: marker needs user approval";

    public NotifyGateRule(Recognition rec) {
        super(rec);
    }

    @Override
    void check(String file, CompilationUnit cu, MarkerIndex markers, CatchClause cc, List<Finding> out) {
        if (Sources.isTestFile(file) || !broadCatch(cc) || Markers.noReportAbove(markers, cc)) {
            return;
        }
        String caught = cc.getParameter().getNameAsString();
        MethodCallExpr notify = Frame.scan(cc.getBody()).calls.stream()
                .filter(call -> rec.notifies(cu, call, caught))
                .findFirst()
                .orElse(null);
        if (notify == null) {
            return;
        }
        Position p = notify.getBegin().orElseThrow();
        out.add(new Finding(ID, file, p.line, p.column, p.line, p.column, MESSAGE));
    }

    private static boolean broadCatch(CatchClause cc) {
        Type t = cc.getParameter().getType();
        if (t instanceof UnionType u) {
            for (ReferenceType part : u.getElements()) {
                if (isBroad(part)) {
                    return true;
                }
            }
            return false;
        }
        return isBroad(t);
    }

    private static boolean isBroad(Type t) {
        return t instanceof ClassOrInterfaceType cit && BROAD.contains(cit.getNameAsString());
    }
}

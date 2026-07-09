package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.stmt.CatchClause;
import java.util.ArrayList;
import java.util.List;
import nl.tsym.tackbox.javalint.Finding;
import nl.tsym.tackbox.javalint.MarkerIndex;
import nl.tsym.tackbox.javalint.Recognition;

/** JV001 (swallow): every execution path through a catch must propagate the
 *  exception (a `throw` in its own frame), report it (a tier-1/tier-2 capture
 *  receiving the caught), print it (a printing terminal), or be covered by a
 *  `// no-report: <reason>` marker - above the catch body for every path, or
 *  above a statement for the paths executing it. Judged per path (the spec's
 *  same-branch doctrine): a guard's throw covers only its own leg, so a silent
 *  fall-through or else leg still swallows. */
public final class SwallowRule {

    public static final String ID = "JV001";

    private static final String MESSAGE =
            ID + ": a catch path swallows the exception; every path must propagate with"
            + " `throw`, report or print the caught, or carry `// no-report: <reason>`";

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
        if (Markers.noReportAbove(markers, cc)) {
            return true;
        }
        String caught = cc.getParameter().getNameAsString();
        return !Flow.hasSilentPath(cc.getBody(),
                call -> rec.capturesOrPrints(cu, call, caught), markers);
    }
}

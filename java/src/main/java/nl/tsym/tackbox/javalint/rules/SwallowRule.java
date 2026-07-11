package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.stmt.CatchClause;
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
public final class SwallowRule extends CatchRule {

    public static final String ID = "JV001";

    private static final String MESSAGE =
            ID + ": a catch path swallows the exception; every path must propagate with"
            + " `throw`, report or print the caught, or carry `// no-report: <reason>`";

    public SwallowRule(Recognition rec) {
        super(rec);
    }

    @Override
    void check(String file, CompilationUnit cu, MarkerIndex markers, CatchClause cc, List<Finding> out) {
        int silent = silentEnd(cu, cc, markers);
        if (silent < 0) {
            return;
        }
        Position p = cc.getBegin().orElseThrow();
        out.add(new Finding(ID, file, p.line, p.column, p.line, p.column,
                MESSAGE + " (a silent path ends at line " + silent + ")"
                        + Markers.deadNoReportHint(markers, cc)));
    }

    /** The line the first silent path ends on, or -1 for a clean catch. */
    private int silentEnd(CompilationUnit cu, CatchClause cc, MarkerIndex markers) {
        if (Markers.noReportAbove(markers, cc)) {
            return -1;
        }
        String caught = cc.getParameter().getNameAsString();
        return Flow.silentPathEnd(cc.getBody(),
                call -> rec.capturesOrPrints(cu, call, caught), markers);
    }
}

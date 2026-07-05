package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.CatchClause;
import com.github.javaparser.ast.stmt.ThrowStmt;
import java.util.ArrayList;
import java.util.List;
import nl.tsym.tackbox.javalint.Finding;
import nl.tsym.tackbox.javalint.Marker;
import nl.tsym.tackbox.javalint.MarkerIndex;

/** JV001 (swallow), minimal form for F8a: a catch must propagate the exception
 *  (contain a `throw`) or carry a `// no-report: <reason>` marker. Capture via
 *  reporters, printing terminals, and object-flow chain are later sessions
 *  (F8b/F8c); this scaffold only distinguishes throw / marker / swallow. */
public final class SwallowRule {

    public static final String ID = "JV001";

    private static final String MESSAGE =
            ID + ": catch swallows the exception; propagate it with `throw`"
            + " or carry `// no-report: <reason>`";

    public List<Finding> check(String file, CompilationUnit cu) {
        MarkerIndex markers = new MarkerIndex(cu);
        List<Finding> out = new ArrayList<>();
        for (CatchClause cc : cu.findAll(CatchClause.class)) {
            if (propagates(cc) || suppressed(cc, markers)) {
                continue;
            }
            Position p = cc.getBegin().orElseThrow();
            out.add(new Finding(ID, file, p.line, p.column, p.line, p.column, MESSAGE));
        }
        return out;
    }

    /** Minimal: any `throw` syntactically inside the catch body counts as
     *  propagation. Path-sensitivity and object-flow tighten this in F8b/F8c. */
    private static boolean propagates(CatchClause cc) {
        return !cc.getBody().findAll(ThrowStmt.class).isEmpty();
    }

    /** A no-report marker directly above the first body statement (or above the
     *  catch clause itself when the body is empty). */
    private static boolean suppressed(CatchClause cc, MarkerIndex markers) {
        BlockStmt body = cc.getBody();
        Position anchor = body.getStatements().isEmpty()
                ? cc.getBegin().orElseThrow()
                : body.getStatement(0).getBegin().orElseThrow();
        Marker m = markers.above(anchor.line);
        return m != null && m.kind() == Marker.Kind.NO_REPORT;
    }
}

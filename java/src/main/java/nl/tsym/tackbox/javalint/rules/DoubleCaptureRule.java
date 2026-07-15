package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.stmt.CatchClause;
import com.github.javaparser.ast.stmt.ThrowStmt;
import java.util.ArrayList;
import java.util.List;
import nl.tsym.tackbox.javalint.Finding;
import nl.tsym.tackbox.javalint.Recognition;

/** JV006 (double capture, port of ERC005): no execution path through a catch
 *  may both report the caught (a tier-1 / tier-2 capture) and rethrow it. The
 *  upstream handler reports the propagated exception a second time, inflating
 *  the error backend - each path picks one: report and swallow, or propagate
 *  without reporting. Judged per path (the spec's same-branch doctrine, as in
 *  ERC005): a terminal guard's throw and a capture on the fall-through are
 *  exclusive legs, not a double. The propagation leg only needs the caught to
 *  reach the throw (even stringified: a wrapped exception still reaches
 *  upstream carrying the failure). A printing terminal is not a backend
 *  capture. */
public final class DoubleCaptureRule {

    public static final String ID = "JV006";

    private static final String MESSAGE =
            ID + ": a catch path both reports the caught and rethrows it; upstream"
            + " reports it again - pick one";

    private static final String LANE_MESSAGE =
            ID + ": a catch path both captures the caught and notifies with it;"
            + " error/warn already reach the user lane, so the notify double-shows"
            + " - drop the notify, or use only notify with no capture";

    private final Recognition rec;

    public DoubleCaptureRule(Recognition rec) {
        this.rec = rec;
    }

    public List<Finding> check(String file, CompilationUnit cu) {
        List<Finding> out = new ArrayList<>();
        for (CatchClause cc : cu.findAll(CatchClause.class)) {
            String caught = cc.getParameter().getNameAsString();
            Flow.Double d = Flow.doubleCapture(cc.getBody(),
                    call -> rec.captures(cu, call, caught),
                    ts -> propagates(ts, caught));
            if (d != null) {
                Position p = cc.getBegin().orElseThrow();
                out.add(new Finding(ID, file, p.line, p.column, p.line, p.column,
                        MESSAGE + " (reported at line " + d.reportLine()
                                + ", rethrown at line " + d.rethrowLine() + ")"));
            }
            // Double-lane (D006): a capture (error/warn/quiet or tier-2, panic
            // excluded - it is terminal) paired with a notify on one path.
            Flow.Lane lane = Flow.laneConflict(cc.getBody(),
                    call -> rec.captures(cu, call, caught)
                            && !"panic".equals(rec.reportVerb(cu, call)),
                    call -> rec.notifies(cu, call, caught));
            if (lane != null) {
                Position p = cc.getBegin().orElseThrow();
                out.add(new Finding(ID, file, p.line, p.column, p.line, p.column,
                        LANE_MESSAGE + " (captured at line " + lane.captureLine()
                                + ", notified at line " + lane.notifyLine() + ")"));
            }
        }
        return out;
    }

    /** The caught reaches this throw - as the object or wrapped, and even
     *  stringified: any propagated exception reaches the upstream handler. */
    private static boolean propagates(ThrowStmt ts, String caught) {
        return ts.getExpression().findFirst(NameExpr.class,
                ne -> ne.getNameAsString().equals(caught)).isPresent();
    }
}

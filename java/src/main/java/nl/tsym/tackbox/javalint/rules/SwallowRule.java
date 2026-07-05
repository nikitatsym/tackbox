package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.LambdaExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.CatchClause;
import com.github.javaparser.ast.stmt.LocalClassDeclarationStmt;
import com.github.javaparser.ast.stmt.ThrowStmt;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;
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

    /** Minimal: any `throw` inside the catch body's own synchronous control
     *  flow counts as propagation. Path-sensitivity and object-flow tighten
     *  this in F8b/F8c. */
    private static boolean propagates(CatchClause cc) {
        ThrowFinder finder = new ThrowFinder();
        cc.getBody().accept(finder, null);
        return finder.found;
    }

    /** Finds a `throw` reachable via the catch body's own synchronous frame,
     *  refusing to descend into a nested scope (lambda, anonymous/local class,
     *  method) the way go/analyzers/recoverswallow refuses to descend into a
     *  FuncLit: a throw there executes later, not as part of this catch. */
    private static final class ThrowFinder extends VoidVisitorAdapter<Void> {
        private boolean found;

        @Override
        public void visit(ThrowStmt n, Void arg) {
            found = true;
        }

        @Override
        public void visit(LambdaExpr n, Void arg) {
            // scope boundary: a lambda body runs in its own frame.
        }

        @Override
        public void visit(LocalClassDeclarationStmt n, Void arg) {
            // scope boundary: a local class's methods run in their own frame.
        }

        @Override
        public void visit(MethodDeclaration n, Void arg) {
            // scope boundary: a nested method runs in its own frame.
        }

        @Override
        public void visit(ObjectCreationExpr n, Void arg) {
            if (n.getAnonymousClassBody().isPresent()) {
                return; // scope boundary: anonymous class body.
            }
            super.visit(n, arg);
        }
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

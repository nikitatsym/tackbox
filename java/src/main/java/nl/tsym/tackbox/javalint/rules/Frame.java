package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.LambdaExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.LocalClassDeclarationStmt;
import com.github.javaparser.ast.stmt.ThrowStmt;
import com.github.javaparser.ast.visitor.VoidVisitorAdapter;
import java.util.ArrayList;
import java.util.List;

/** A catch body's own synchronous frame: the throws and the calls that execute
 *  as part of this catch, in document order. Nested scopes (lambda, anonymous /
 *  local class, nested method) are not descended - code there runs in its own
 *  frame later, the way go/analyzers refuse to descend into a FuncLit. */
final class Frame extends VoidVisitorAdapter<Void> {

    boolean hasThrow;
    final List<MethodCallExpr> calls = new ArrayList<>();

    static Frame scan(BlockStmt body) {
        Frame f = new Frame();
        body.accept(f, null);
        return f;
    }

    @Override
    public void visit(ThrowStmt n, Void arg) {
        hasThrow = true;
        super.visit(n, arg);
    }

    @Override
    public void visit(MethodCallExpr n, Void arg) {
        calls.add(n);
        super.visit(n, arg);
    }

    @Override
    public void visit(LambdaExpr n, Void arg) {}

    @Override
    public void visit(LocalClassDeclarationStmt n, Void arg) {}

    @Override
    public void visit(MethodDeclaration n, Void arg) {}

    @Override
    public void visit(ObjectCreationExpr n, Void arg) {
        if (n.getAnonymousClassBody().isPresent()) {
            return;
        }
        super.visit(n, arg);
    }
}

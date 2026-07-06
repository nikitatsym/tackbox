package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.expr.BinaryExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.StringLiteralExpr;

/** The F5 object-flow primitive ported to Java (mirror of astutil.errObjectFlows):
 *  does the caught reach `root` as a live object - a bare reference outside any
 *  stringifying construct? A subtree that stringifies its content (`getMessage` /
 *  `toString`, `String.valueOf`, a string concatenation) is pruned; the caught
 *  inside it is a stringified occurrence, not object flow. A constructor or method
 *  that receives the caught object is trusted to carry it - its chain contract is
 *  not verified - so the chain breaks only when every occurrence of the caught in
 *  the carrier passes through a string. */
final class ObjectFlow {

    private ObjectFlow() {}

    static boolean flows(Node root, String caught) {
        if (stringifies(root)) {
            return false;
        }
        if (root instanceof NameExpr ne && ne.getNameAsString().equals(caught)) {
            return true;
        }
        for (Node child : root.getChildNodes()) {
            if (flows(child, caught)) {
                return true;
            }
        }
        return false;
    }

    /** A node that converts its content to a string: a no-arg `getMessage` /
     *  `getLocalizedMessage` / `toString`, a `String.valueOf(...)`, or a string
     *  concatenation (a `+` carrying a string literal). */
    private static boolean stringifies(Node node) {
        if (node instanceof MethodCallExpr call) {
            String m = call.getNameAsString();
            if (call.getArguments().isEmpty()
                    && (m.equals("getMessage") || m.equals("getLocalizedMessage") || m.equals("toString"))) {
                return true;
            }
            return m.equals("valueOf") && call.getScope()
                    .filter(s -> s instanceof NameExpr ne && ne.getNameAsString().equals("String")).isPresent();
        }
        return node instanceof BinaryExpr be
                && be.getOperator() == BinaryExpr.Operator.PLUS
                && be.findFirst(StringLiteralExpr.class).isPresent();
    }
}

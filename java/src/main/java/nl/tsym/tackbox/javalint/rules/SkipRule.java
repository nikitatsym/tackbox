package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.MarkerAnnotationExpr;
import com.github.javaparser.ast.expr.MemberValuePair;
import com.github.javaparser.ast.expr.NormalAnnotationExpr;
import com.github.javaparser.ast.expr.SingleMemberAnnotationExpr;
import com.github.javaparser.ast.expr.StringLiteralExpr;
import java.util.ArrayList;
import java.util.List;
import nl.tsym.tackbox.javalint.Finding;

/** JV007 (skip): a disabled test must carry a non-empty reason. A `@Disabled`
 *  (JUnit 5) or `@Ignore` (JUnit 4) on a method or class fires when it is a bare
 *  marker (`@Disabled`), or its value is an empty / whitespace-only string
 *  literal (`@Disabled("")`, `@Ignore(" ")`, `@Disabled(value = "")`). A
 *  non-empty string value is clean; a non-literal value expression (a constant
 *  reference, a concatenation) is trusted and left clean. There is no
 *  marker escape for Java - the fix is always to put the reason into
 *  the annotation value.
 *
 *  <p>Matching is by the annotation's simple name only: javaparser here runs
 *  without full symbol solving, so an unrelated `@Disabled` from another library
 *  would also match. That is deliberate - the false-positive cost is a one-word
 *  reason string, and disabling tests silently is the failure we guard. */
public final class SkipRule {

    public static final String ID = "JV007";

    private static final String MESSAGE =
            ID + ": `@Disabled`/`@Ignore` must carry a non-empty reason"
            + " (\"why is this test off\")";

    public List<Finding> check(String file, CompilationUnit cu) {
        List<Finding> out = new ArrayList<>();
        for (AnnotationExpr ann : cu.findAll(AnnotationExpr.class)) {
            if (!isSkipAnnotation(ann) || !onMethodOrClass(ann) || !missingReason(ann)) {
                continue;
            }
            Position p = ann.getBegin().orElseThrow();
            out.add(new Finding(ID, file, p.line, p.column, p.line, p.column, MESSAGE));
        }
        return out;
    }

    private static boolean isSkipAnnotation(AnnotationExpr ann) {
        String name = ann.getNameAsString();
        return name.equals("Disabled") || name.equals("Ignore");
    }

    private static boolean onMethodOrClass(AnnotationExpr ann) {
        Node parent = ann.getParentNode().orElse(null);
        return parent instanceof MethodDeclaration
                || parent instanceof ClassOrInterfaceDeclaration;
    }

    /** No reason: a bare marker, an empty single value, or a normal form whose
     *  `value` member is absent or an empty string literal. A non-literal value
     *  expression is trusted and reports no missing reason. */
    private static boolean missingReason(AnnotationExpr ann) {
        if (ann instanceof MarkerAnnotationExpr) {
            return true;
        }
        if (ann instanceof SingleMemberAnnotationExpr single) {
            return isEmptyStringLiteral(single.getMemberValue());
        }
        if (ann instanceof NormalAnnotationExpr normal) {
            Expression value = valueMember(normal);
            return value == null || isEmptyStringLiteral(value);
        }
        return false;
    }

    private static Expression valueMember(NormalAnnotationExpr normal) {
        for (MemberValuePair pair : normal.getPairs()) {
            if (pair.getNameAsString().equals("value")) {
                return pair.getValue();
            }
        }
        return null;
    }

    private static boolean isEmptyStringLiteral(Expression value) {
        return value instanceof StringLiteralExpr literal
                && literal.getValue().trim().isEmpty();
    }
}

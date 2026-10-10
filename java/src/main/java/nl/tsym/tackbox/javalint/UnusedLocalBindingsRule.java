package nl.tsym.tackbox.javalint;

import net.sourceforge.pmd.lang.java.ast.ASTLocalVariableDeclaration;
import net.sourceforge.pmd.lang.java.ast.ASTVariableId;
import net.sourceforge.pmd.lang.java.ast.internal.JavaAstUtils;
import net.sourceforge.pmd.lang.java.rule.bestpractices.UnusedLocalVariableRule;

public final class UnusedLocalBindingsRule extends UnusedLocalVariableRule {

    @Override
    public Object visit(ASTLocalVariableDeclaration declaration, Object data) {
        for (ASTVariableId variable : declaration.getVarIds()) {
            boolean forcedDiscard = (variable.isForeachVariable() || variable.isResourceDeclaration())
                    && variable.getName().startsWith("_");
            if (!forcedDiscard && JavaAstUtils.isNeverUsed(variable)) {
                asCtx(data).addViolation(variable, variable.getName());
            }
        }
        return data;
    }
}

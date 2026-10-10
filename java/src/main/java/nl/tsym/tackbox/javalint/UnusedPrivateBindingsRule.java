package nl.tsym.tackbox.javalint;

import java.util.HashSet;
import java.util.Set;
import net.sourceforge.pmd.lang.java.ast.ASTCompilationUnit;
import net.sourceforge.pmd.lang.java.ast.ASTExpression;
import net.sourceforge.pmd.lang.java.ast.ASTMethodCall;
import net.sourceforge.pmd.lang.java.rule.bestpractices.UnusedPrivateMethodRule;
import net.sourceforge.pmd.lang.java.types.TypeOps;

public final class UnusedPrivateBindingsRule extends UnusedPrivateMethodRule {

    private record Call(String name, int arity) {}

    private final Set<Call> uncertainCalls = new HashSet<>();

    @Override
    public Object visit(ASTCompilationUnit file, Object data) {
        uncertainCalls.clear();
        for (ASTMethodCall call : file.descendants(ASTMethodCall.class).crossFindBoundaries()) {
            if (call.getOverloadSelectionInfo().isFailed()
                    || call.getArguments().children(ASTExpression.class).any(argument ->
                            TypeOps.isUnresolvedOrArray(argument.getTypeMirror()))) {
                uncertainCalls.add(new Call(call.getMethodName(), call.getArguments().size()));
            }
        }
        return super.visit(file, data);
    }

    boolean hasUncertainCall(String name, int arity) {
        return uncertainCalls.contains(new Call(name, arity));
    }
}

package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.FieldAccessExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import nl.tsym.tackbox.javalint.Finding;
import nl.tsym.tackbox.javalint.Recognition;

/** JV008 (fingerprint secret-arg, port of ERC006): a call javalint recognizes
 *  as a capture sink by origin (tier-1 logger error/warn, or a tier-2 declared
 *  reporter - the same set {@link Recognition#isCaptureSink} answers, never a
 *  bare method name) must not receive an argument that deep-contains a name
 *  whose simple name matches a secret stop-word (token/password/key/secret/
 *  cookie, case-insensitive substring). The NAME carries the live value into
 *  telemetry; a stop-word inside a string literal is domain prose, not a value,
 *  and stays clean - mirroring the Go/Python/JS behavior. Unlike the swallow
 *  rules this is not catch-scoped: every recognized sink call is scanned,
 *  regardless of whether a caught error reaches it, and there is no
 *  `// no-report` escape - the fix is to drop the secret name. */
public final class FingerprintRule {

    public static final String ID = "JV008";

    // README: token/password/key/secret/cookie, matched case-insensitively.
    private static final List<String> STOP_WORDS =
            List.of("token", "password", "key", "secret", "cookie");

    private final Recognition rec;

    public FingerprintRule(Recognition rec) {
        this.rec = rec;
    }

    public List<Finding> check(String file, CompilationUnit cu) {
        List<Finding> out = new ArrayList<>();
        for (MethodCallExpr call : cu.findAll(MethodCallExpr.class)) {
            if (!rec.isCaptureSink(cu, call)) {
                continue;
            }
            for (Expression arg : call.getArguments()) {
                secretCarrier(arg).ifPresent(node -> out.add(finding(file, node)));
            }
        }
        return out;
    }

    private static Finding finding(String file, Node node) {
        Position p = node.getBegin().orElseThrow();
        Position e = node.getEnd().orElseThrow();
        return new Finding(ID, file, p.line, p.column, e.line, e.column,
                ID + ": capture arg names a secret (" + carrierName(node) + "); the name"
                + " carries the value into telemetry - pass non-secret context");
    }

    /** The first name-carrying node in `arg`'s subtree whose simple name matches
     *  a stop-word, in document order. Only names carry a value: a NameExpr, a
     *  FieldAccessExpr's field, or a MethodCallExpr's method (a `getSecret()`
     *  getter leaks like a field - parity with the Go/JS ident scan). A string
     *  literal is not a carrier, so prose in a message stays clean. */
    private static Optional<Node> secretCarrier(Expression arg) {
        return arg.stream()
                .filter(n -> matchesStopWord(carrierName(n)))
                .findFirst();
    }

    /** The simple name a node contributes as a value carrier, or null when the
     *  node names no value (a literal, a `this`, a type). */
    private static String carrierName(Node n) {
        if (n instanceof NameExpr ne) {
            return ne.getNameAsString();
        }
        if (n instanceof FieldAccessExpr fa) {
            return fa.getNameAsString();
        }
        if (n instanceof MethodCallExpr mc) {
            return mc.getNameAsString();
        }
        return null;
    }

    private static boolean matchesStopWord(String name) {
        if (name == null) {
            return false;
        }
        String lower = name.toLowerCase(Locale.ROOT);
        for (String w : STOP_WORDS) {
            if (lower.contains(w)) {
                return true;
            }
        }
        return false;
    }
}

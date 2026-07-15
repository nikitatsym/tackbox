package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.Position;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.StringLiteralExpr;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;
import nl.tsym.tackbox.javalint.Finding;
import nl.tsym.tackbox.javalint.Recognition;

/** JV010 (user-lane argument contract, D007 + D008): a Report user-lane verb
 *  must carry a static-literal msg and a well-formed literal dedupKey. The msg
 *  is what the user sees and titles the issue, so dynamic data belongs in cause
 *  and tags; the dedupKey is the Sentry fingerprint and the coalescing key, so
 *  it must be stable. msg-static covers error/warn/notify (quiet is
 *  telemetry-only, panic takes a name); the dedupKey contract covers
 *  error/warn/quiet/notify. Recognized by the Report origin, like the capture
 *  rules - slf4j / System.Logger sinks carry their own contract and are out of
 *  scope. */
public final class ReporterArgsRule {

    public static final String ID = "JV010";

    private static final Set<String> MSG_VERBS = Set.of("error", "warn", "notify");
    private static final Set<String> DEDUP_VERBS = Set.of("error", "warn", "quiet", "notify");
    private static final Pattern DEDUP_KEY =
            Pattern.compile("^[a-z][a-z0-9_-]*\\.[a-z][a-z0-9_-]*(:[a-zA-Z0-9_.-]+)?$");

    private static final String MSG_STATIC =
            ID + ": msg must be a static string literal; dynamic data belongs in cause and tags";
    private static final String DEDUP_MISSING =
            ID + ": dedupKey is required - it is the Sentry fingerprint and the coalescing key";
    private static final String DEDUP_NOT_LITERAL =
            ID + ": dedupKey must be a static string literal so the fingerprint is stable";
    private static final String DEDUP_BAD_FORMAT =
            ID + ": dedupKey must match area.suffix[:identifier]";

    private final Recognition rec;

    public ReporterArgsRule(Recognition rec) {
        this.rec = rec;
    }

    public List<Finding> check(String file, CompilationUnit cu) {
        List<Finding> out = new ArrayList<>();
        if (Sources.isTestFile(file)) {
            return out;
        }
        for (MethodCallExpr call : cu.findAll(MethodCallExpr.class)) {
            String verb = rec.reportVerb(cu, call);
            if (verb == null) {
                continue;
            }
            if (MSG_VERBS.contains(verb)) {
                checkMsg(file, call, out);
            }
            if (DEDUP_VERBS.contains(verb)) {
                checkDedup(file, call, out);
            }
        }
        return out;
    }

    private void checkMsg(String file, MethodCallExpr call, List<Finding> out) {
        List<Expression> args = call.getArguments();
        if (args.isEmpty() || args.get(0) instanceof StringLiteralExpr) {
            return;
        }
        add(file, args.get(0), MSG_STATIC, out);
    }

    private void checkDedup(String file, MethodCallExpr call, List<Finding> out) {
        List<Expression> args = call.getArguments();
        if (args.size() < 4) {
            add(file, call, DEDUP_MISSING, out);
            return;
        }
        Expression key = args.get(3);
        if (!(key instanceof StringLiteralExpr lit)) {
            add(file, key, DEDUP_NOT_LITERAL, out);
        } else if (!DEDUP_KEY.matcher(lit.getValue()).matches()) {
            add(file, key, DEDUP_BAD_FORMAT, out);
        }
    }

    private static void add(String file, Node at, String message, List<Finding> out) {
        Position p = at.getBegin().orElseThrow();
        out.add(new Finding(ID, file, p.line, p.column, p.line, p.column, message));
    }
}

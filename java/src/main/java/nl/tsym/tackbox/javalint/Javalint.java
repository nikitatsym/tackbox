package nl.tsym.tackbox.javalint;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ast.CompilationUnit;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import nl.tsym.tackbox.javalint.rules.SwallowRule;

/** javalint CLI. Emits erclint-shaped JSON on stdout and exits 0 even with
 *  findings - the python CLI aggregates findings into the failing exit, exactly
 *  as it does for erclint's `-json` output. */
public final class Javalint {

    static final String VERSION = version();

    private Javalint() {}

    public static void main(String[] args) throws IOException {
        List<String> files = new ArrayList<>();
        for (String arg : args) {
            if (arg.equals("--version") || arg.equals("-version")) {
                System.out.println("javalint " + VERSION);
                return;
            }
            // --reporters= is accepted for CLI parity; tier-1/2 recognition is
            // F8b, so the minimal JV001 does not consume it yet.
            if (arg.startsWith("--")) {
                continue;
            }
            files.add(arg);
        }
        List<Finding> findings = new ArrayList<>();
        for (String f : files) {
            findings.addAll(analyze(Path.of(f)));
        }
        System.out.print(JsonWriter.write(findings));
    }

    public static List<Finding> analyze(Path path) throws IOException {
        return analyze(path.toString(), Files.readString(path));
    }

    /** Parse `content` under `name` and run the rule set. A parse failure is a
     *  hard, loud error (no silent skip); compile-broken handling is F8c. */
    public static List<Finding> analyze(String name, String content) {
        ParseResult<CompilationUnit> result = new JavaParser().parse(content);
        CompilationUnit cu = result.getResult().orElseThrow(
                () -> new IllegalArgumentException(
                        "cannot parse " + name + ": " + result.getProblems()));
        return new SwallowRule().check(name, cu);
    }

    private static String version() {
        String v = Javalint.class.getPackage().getImplementationVersion();
        return v == null ? "dev" : v;
    }
}

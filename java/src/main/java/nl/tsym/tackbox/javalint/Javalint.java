package nl.tsym.tackbox.javalint;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ast.CompilationUnit;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import nl.tsym.tackbox.javalint.rules.ChainRule;
import nl.tsym.tackbox.javalint.rules.DoubleCaptureRule;
import nl.tsym.tackbox.javalint.rules.ExitRule;
import nl.tsym.tackbox.javalint.rules.SwallowRule;
import nl.tsym.tackbox.javalint.rules.ThrowableRule;
import nl.tsym.tackbox.javalint.rules.UselessCatchRule;

/** javalint CLI. Emits erclint-shaped JSON on stdout and exits 0 even with
 *  findings - the python CLI aggregates findings into the failing exit, exactly
 *  as it does for erclint's `-json` output. */
public final class Javalint {

    static final String VERSION = version();

    private Javalint() {}

    public static void main(String[] args) throws IOException {
        List<String> files = new ArrayList<>();
        List<Reporters.Resolved> reporters = List.of();
        for (String arg : args) {
            if (arg.equals("--version") || arg.equals("-version")) {
                System.out.println("javalint " + VERSION);
                return;
            }
            if (arg.startsWith("--reporters=")) {
                reporters = resolveReporters(arg.substring("--reporters=".length()));
                continue;
            }
            if (arg.startsWith("--")) {
                continue;
            }
            files.add(arg);
        }
        List<Finding> findings = new ArrayList<>();
        for (String f : files) {
            findings.addAll(analyze(Path.of(f), reporters));
        }
        System.out.print(JsonWriter.write(findings));
    }

    public static List<Finding> analyze(Path path, List<Reporters.Resolved> reporters) throws IOException {
        return analyze(path.toString(), Files.readString(path), reporters);
    }

    public static List<Finding> analyze(String name, String content) {
        return analyze(name, content, List.of());
    }

    /** Parse `content` under `name` and run the rule set. A parse failure is a
     *  hard, loud error (no silent skip); compile-broken handling is F8d. */
    public static List<Finding> analyze(String name, String content, List<Reporters.Resolved> reporters) {
        ParseResult<CompilationUnit> result = new JavaParser().parse(content);
        CompilationUnit cu = result.getResult().orElseThrow(
                () -> new IllegalArgumentException(
                        "cannot parse " + name + ": " + result.getProblems()));
        MarkerIndex markers = new MarkerIndex(cu);
        Recognition rec = new Recognition(reporters);
        List<Finding> out = new ArrayList<>();
        out.addAll(new SwallowRule(rec).check(name, cu, markers));
        out.addAll(new ChainRule().check(name, cu, markers));
        out.addAll(new ThrowableRule().check(name, cu, markers));
        out.addAll(new UselessCatchRule().check(name, cu));
        out.addAll(new ExitRule(rec).check(name, cu, markers));
        out.addAll(new DoubleCaptureRule(rec).check(name, cu));
        return out;
    }

    /** Read each declared reporter file (cwd-relative, as the python CLI passes
     *  repo-relative paths with cwd at the repo root) to resolve its package. */
    private static List<Reporters.Resolved> resolveReporters(String spec) throws IOException {
        List<Reporters.Resolved> out = new ArrayList<>();
        for (Reporters.Declared d : Reporters.parse(spec)) {
            out.add(Reporters.resolve(d, Files.readString(Path.of(d.file()))));
        }
        return out;
    }

    private static String version() {
        String v = Javalint.class.getPackage().getImplementationVersion();
        return v == null ? "dev" : v;
    }
}

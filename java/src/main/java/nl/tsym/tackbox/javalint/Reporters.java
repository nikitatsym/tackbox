package nl.tsym.tackbox.javalint;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ast.CompilationUnit;
import java.util.ArrayList;
import java.util.List;

/** Parses the `--reporters=<file>#<Class.method>,...` transport the python CLI
 *  builds from `.tackbox-reporters`, and resolves each declared file to the
 *  package it lives in. Recognition is package-aware: a same-named class in
 *  another package is not the declared reporter, so the declaration is pinned to
 *  the package parsed from its own file, not to the class name alone. */
public final class Reporters {

    /** A parsed transport entry: the declared file and the class#method in it. */
    public record Declared(String file, String className, String method) {}

    /** A declaration resolved to the package its file declares - the identity a
     *  call site is matched against. */
    public record Resolved(String packageName, String className, String method) {}

    private Reporters() {}

    public static List<Declared> parse(String spec) {
        List<Declared> out = new ArrayList<>();
        for (String entry : spec.split(",")) {
            String e = entry.strip();
            if (e.isEmpty()) {
                continue;
            }
            int hash = e.lastIndexOf('#');
            int dot = e.lastIndexOf('.');
            if (hash <= 0 || dot <= hash + 1 || dot == e.length() - 1) {
                throw new IllegalArgumentException(
                        "--reporters: malformed declaration '" + e + "' (want <file>#<Class.method>)");
            }
            out.add(new Declared(e.substring(0, hash), e.substring(hash + 1, dot), e.substring(dot + 1)));
        }
        return out;
    }

    /** Resolve a declaration against its file's source: the package is read from
     *  the file's `package` statement (default package = ""). The class/method
     *  are taken from the entry; the python CLI already checked the file exists. */
    public static Resolved resolve(Declared d, String fileContent) {
        CompilationUnit cu = new JavaParser().parse(fileContent).getResult().orElseThrow(
                () -> new IllegalArgumentException(
                        "--reporters: cannot parse declared file " + d.file()));
        String pkg = cu.getPackageDeclaration().map(pd -> pd.getNameAsString()).orElse("");
        return new Resolved(pkg, d.className(), d.method());
    }
}

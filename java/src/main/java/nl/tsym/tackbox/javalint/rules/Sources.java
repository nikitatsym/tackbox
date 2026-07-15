package nl.tsym.tackbox.javalint.rules;

/** Source-tree helpers shared by rules. */
final class Sources {

    private Sources() {}

    /** True iff `name` is a Maven/Gradle test source (under a `src/test/`
     *  directory). The reporter-usage rules (JV009 notify gate, JV010 argument
     *  contract) skip tests - the Java analog of erclint skipping `*_test.go`:
     *  the msg/dedupKey and notify contracts govern production call sites, not
     *  test scaffolding exercising the helper with dynamic keys. */
    static boolean isTestFile(String name) {
        String p = name.replace('\\', '/');
        return p.contains("/src/test/") || p.startsWith("src/test/");
    }
}

package nl.tsym.tackbox.javalint;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.github.javaparser.ast.CompilationUnit;
import com.google.gson.JsonElement;
import com.google.gson.JsonParser;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;

class UnusedBindingsTest {

    @Test
    void findingsKeepRuleIdsAndJsonPositions() {
        String source = "class S {\n  private int value;\n  private void helper() {}\n"
                + "  void run() { int local = 0; }\n}";
        List<Finding> findings = Javalint.analyze("src/S.java", source);
        Set<String> positions = new HashSet<>();
        var rules = JsonParser.parseString(JsonWriter.write(findings)).getAsJsonObject()
                .getAsJsonObject("src/S.java");
        rules.entrySet().forEach(entry -> {
            for (JsonElement element : entry.getValue().getAsJsonArray()) {
                var diagnostic = element.getAsJsonObject();
                String position = diagnostic.get("posn").getAsString();
                positions.add(entry.getKey() + ":" + position.substring(0, position.lastIndexOf(':')));
                assertTrue(diagnostic.get("message").getAsString().startsWith(entry.getKey() + ":"));
                assertTrue(diagnostic.get("end").getAsString().startsWith("src/S.java:"));
            }
        });
        assertEquals(Set.of("JV012:src/S.java:2", "JV013:src/S.java:3", "JV011:src/S.java:4"), positions);
        assertEquals(3, findings.size());
        assertTrue(findings.stream().allMatch(f -> f.column() > 0 && f.endColumn() >= f.column()));
        assertTrue(findings.stream().noneMatch(Finding::suppressed));
    }

    @Test
    void pmdAnnotationsAndCommentsDoNotSuppressFindings() {
        String source = "@SuppressWarnings(\"PMD\") class S {\n"
                + "  private int value; // NOPMD\n"
                + "  @SuppressWarnings(\"PMD.JV013\") private void helper() {}\n"
                + "  void run() {\n"
                + "    // no-report: this marker belongs to error handling\n"
                + "    int local = 0; // NOPMD\n"
                + "  }\n}";
        List<Finding> findings = Javalint.analyze("S.java", source);
        assertEquals(Set.of("JV012", "JV013", "JV011"), new HashSet<>(findings.stream().map(Finding::rule).toList()));
        assertEquals(3, findings.size());
        assertTrue(findings.stream().noneMatch(Finding::suppressed));
    }

    @Test
    void usedMembersStayCleanIncludingMethodReferencesAndNestedClasses() {
        String source = "class S { private int value; private void helper() { value++; }"
                + " Runnable run() { return this::helper; }"
                + " class Inner { int read() { return value; } } }";
        assertTrue(Javalint.analyze("S.java", source).isEmpty());
    }

    @Test
    void unresolvedOverloadArgumentsDoNotReportCalledMethods() {
        String source = "import com.unknown.Foo; import com.unknown.Bar; import com.unknown.Baz;"
                + " class S { private int over(Foo f) { return 1; } private int over(Bar b) { return 2; }"
                + " int m(Foo f, Baz z) { return over(f) + over(z.getBar()); } }";
        assertTrue(Javalint.analyze("S.java", source).isEmpty());
        String dead = source.replace("return over(f) + over(z.getBar());", "return 0;");
        assertEquals(2, Javalint.analyze("S.java", dead).stream().filter(f -> f.rule().equals("JV013")).count());
    }

    @Test
    void resolvedJdkCallDoesNotHideDeadOverload() {
        String source = "class S {\n"
                + " private int over(String value) { return 1; }\n"
                + " private int over(Integer value) { return 2; }\n"
                + " int run() { return over(\"used\"); }\n}";
        List<Finding> findings = Javalint.analyze("S.java", source);
        assertEquals(Set.of("JV013:3"), findings.stream().map(f -> f.rule() + ":" + f.line())
                .collect(java.util.stream.Collectors.toSet()));
        assertEquals(1, findings.size());
    }

    @Test
    void analyzerClasspathDoesNotExposeBundledLibraries() {
        String source = "import com.google.gson.JsonObject; import com.google.gson.JsonArray;"
                + " class S { private int over(JsonObject f) { return 1; } private int over(JsonArray b) { return 2; }"
                + " int m(JsonObject f) { return over(f.getAsJsonObject()); } }";
        assertEquals(Javalint.analyze("S.java", source).stream().map(Finding::rule).toList(),
                Javalint.analyze("S.java", source.replace("com.google.gson", "com.fake.gson"))
                        .stream().map(Finding::rule).toList());
    }

    @Test
    void privateMembersAndOrdinaryLocalsHaveNoNamingEscape() {
        String source = "class S { private int _field; private void _method() {}"
                + " void run(int unusedParameter) { int _result = 0; int ignoredResult = 0; int unusedResult = 0; } }";
        List<Finding> findings = Javalint.analyze("S.java", source);
        assertEquals(3, findings.stream().filter(f -> f.rule().equals("JV011")).count());
        assertEquals(Set.of("JV011", "JV012", "JV013"), new HashSet<>(findings.stream().map(Finding::rule).toList()));
        assertEquals(5, findings.size());
    }

    @Test
    void analyzerErrorsCannotReturnPartialFindings() {
        IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
                () -> UnusedBindings.check("Broken.java", "class Broken {", new CompilationUnit()));
        assertTrue(error.getMessage().contains("Broken.java"));
        assertFalse(error.getMessage().isBlank());
    }
}

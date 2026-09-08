package nl.tsym.tackbox.javalint;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.File;
import java.util.List;
import org.junit.jupiter.api.Test;

/** Pins the JSON contract: javalint emits erclint's `-json` shape
 *  { "&lt;file&gt;": { "&lt;rule&gt;": [ {posn,end,message} ] } } so the python CLI
 *  parses javalint and erclint through one path (wired in F8d). */
class JsonOutputTest {

    private static final String SWALLOW = String.join("\n",
            "class S {",
            "  void m() {",
            "    try { risky(); } catch (Exception e) { log(e); }",
            "  }",
            "  void risky() throws Exception {}",
            "  void log(Exception e) {}",
            "}");

    @Test
    void jsonMatchesErclintShape() {
        List<Finding> findings = Javalint.analyze("S.java", SWALLOW);
        assertEquals(1, findings.size(), "expected one JV001, got " + findings);

        String json = JsonWriter.write(findings);
        assertTrue(json.contains("\"S.java\": {"), json);
        assertTrue(json.contains("\"JV001\": ["), json);
        assertTrue(json.contains("\"posn\": \"S.java:3:"), json);
        assertTrue(json.contains("\"end\": \"S.java:3:"), json);
        assertTrue(json.contains("\"message\": \"JV001: a catch path swallows"), json);
    }

    @Test
    void suppressedPathKeepsItsRuleAndMarkerWithoutHidingAnotherPath() {
        String source = String.join("\n",
                "class S { void run() {",
                "try { work(); } catch (Exception e) {",
                "if (ready) {",
                "// no-report: caller tolerates this branch",
                "return;",
                "} else { return; }",
                "}",
                "} }");
        List<Finding> findings = Javalint.analyze("S.java", source).stream()
                .filter(f -> f.rule().equals("JV001")).toList();
        assertEquals(2, findings.size());
        Finding suppressed = findings.stream().filter(Finding::suppressed).findFirst().orElseThrow();
        assertEquals(4, suppressed.marker().line());
        assertTrue(suppressed.message().contains("a silent path ends at line 5"));
        assertTrue(findings.stream().anyMatch(f -> !f.suppressed()
                && f.message().contains("a silent path ends at line 6")));
        String json = JsonWriter.write(findings);
        assertTrue(json.contains("\"suppressed\": true, \"marker_kind\": \"no-report\", \"marker_line\": 4"));
        String handled = source.replace("return;", "throw e;");
        assertTrue(Javalint.analyze("S.java", handled).stream().noneMatch(f -> f.rule().equals("JV001")));
    }

    /** A `\` is the separator on Windows and an ordinary file name character
     *  everywhere else, so the same Finding must normalize on Windows and stay
     *  verbatim off it. A real windows Path.toString() would use backslashes; a
     *  unix Path cannot produce that, so the Finding is built directly. */
    @Test
    void separatorNormalizationFollowsThePlatformSeparator() {
        Finding f = new Finding("JV001", "javasub\\Deep.java", 2, 9, 2, 9, "m");
        String json = JsonWriter.write(List.of(f));
        if (File.separatorChar == '\\') {
            assertTrue(json.contains("\"javasub/Deep.java\": {"), json);
            assertTrue(json.contains("\"posn\": \"javasub/Deep.java:2:9\""), json);
            assertTrue(json.contains("\"end\": \"javasub/Deep.java:2:9\""), json);
            assertFalse(json.contains("\\\\"), "no backslash may survive into the JSON: " + json);
        } else {
            assertTrue(json.contains("\"javasub\\\\Deep.java\": {"), json);
            assertTrue(json.contains("\"posn\": \"javasub\\\\Deep.java:2:9\""), json);
            assertFalse(json.contains("javasub/Deep.java"), json);
        }
    }
}

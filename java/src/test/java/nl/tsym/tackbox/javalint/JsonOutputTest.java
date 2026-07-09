package nl.tsym.tackbox.javalint;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

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
    void normalizesWindowsBackslashSeparators() {
        // A real windows Path.toString() would use backslashes here; a unix
        // Path can't produce that, so build the Finding directly with one.
        Finding f = new Finding("JV001", "javasub\\Deep.java", 2, 9, 2, 9, "m");
        String json = JsonWriter.write(List.of(f));
        assertTrue(json.contains("\"javasub/Deep.java\": {"), json);
        assertTrue(json.contains("\"posn\": \"javasub/Deep.java:2:9\""), json);
        assertTrue(json.contains("\"end\": \"javasub/Deep.java:2:9\""), json);
        assertFalse(json.contains("\\\\"), "no backslash may survive into the JSON: " + json);
    }
}

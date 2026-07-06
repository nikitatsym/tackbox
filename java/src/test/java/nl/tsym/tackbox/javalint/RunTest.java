package nl.tsym.tackbox.javalint;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Exit-code contract (F8d-1): a dead or malformed reporter declaration is a hard
 *  error (exit 2), matching erclint's reporters.Resolve; a live symbol resolves
 *  clean (exit 0, even with findings). Drives Javalint.run directly so the
 *  System.exit path stays testable. */
class RunTest {

    private static final String DECL =
            "package a.b; public class Log { public static void report(Throwable t) {} }";
    private static final String TARGET = "class T {}";

    @Test
    void deadReporterSymbolExitsTwo(@TempDir Path dir) throws Exception {
        Path decl = write(dir, "Log.java", DECL);
        int code = Javalint.run(new String[] {"--reporters=" + decl + "#Log.nope", target(dir)});
        assertEquals(2, code, "a dead reporter symbol must be a hard error (exit 2)");
    }

    @Test
    void liveReporterSymbolExitsZero(@TempDir Path dir) throws Exception {
        Path decl = write(dir, "Log.java", DECL);
        int code = Javalint.run(new String[] {"--reporters=" + decl + "#Log.report", target(dir)});
        assertEquals(0, code, "a live reporter symbol must resolve clean");
    }

    @Test
    void malformedReporterExitsTwo(@TempDir Path dir) throws Exception {
        int code = Javalint.run(new String[] {"--reporters=garbage", target(dir)});
        assertEquals(2, code, "a malformed --reporters value must exit 2");
    }

    @Test
    void resolveRejectsDeadSymbol() {
        Reporters.ReportersException e = assertThrows(Reporters.ReportersException.class, () ->
                Reporters.resolve(new Reporters.Declared("Log.java", "Log", "nope"),
                        "package a.b; class Log { void report(Throwable t) {} }"));
        assertTrue(e.getMessage().contains("Log.nope"),
                "message should name the dead symbol: " + e.getMessage());
    }

    @Test
    void resolveAcceptsUnexportedSymbol() {
        // Parity with go reporters.Resolve: an unexported / non-public sink still resolves.
        Reporters.Resolved r = Reporters.resolve(
                new Reporters.Declared("Log.java", "Log", "report"),
                "package a.b; class Log { private void report(Throwable t) {} }");
        assertEquals("a.b", r.packageName());
    }

    private static Path write(Path dir, String name, String content) throws Exception {
        Path p = dir.resolve(name);
        Files.writeString(p, content);
        return p;
    }

    private static String target(Path dir) throws Exception {
        return write(dir, "T.java", TARGET).toString();
    }
}

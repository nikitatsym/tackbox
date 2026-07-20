package nl.tsym.tackbox.javalint;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.TestFactory;

/** analysistest-style harness: each testdata/*.java.txt fixture carries
 *  `// want "&lt;regex&gt;"` on the line a finding is expected, and passes iff the
 *  reported findings match the wants exactly - every want fires, no extras.
 *  The .java.txt extension keeps the deliberately-dirty fixtures out of
 *  tackbox's own opengrep-java self-lint (no committed .java swallows). */
class WantHarnessTest {

    private static final Pattern WANT = Pattern.compile("//\\s*want\\s*\"([^\"]*)\"");
    private static final Pattern REPORTERS = Pattern.compile("//\\s*reporters:\\s*(\\S.*)");

    @TestFactory
    List<DynamicTest> fixtures() throws Exception {
        Path dir = testdataDir();
        List<DynamicTest> tests = new ArrayList<>();
        for (Path fixture : fixtureFiles(dir)) {
            String name = fixture.getFileName().toString();
            String text = Files.readString(fixture);
            tests.add(DynamicTest.dynamicTest(name, () -> checkFixture(name, text, dir)));
        }
        assertTrue(!tests.isEmpty(), "no testdata fixtures found on classpath");
        return tests;
    }

    private static Path testdataDir() throws Exception {
        URL url = WantHarnessTest.class.getResource("/testdata");
        assertNotNull(url, "testdata resource directory missing");
        return Path.of(url.toURI());
    }

    private static List<Path> fixtureFiles(Path dir) throws Exception {
        try (Stream<Path> s = Files.list(dir)) {
            return s.filter(p -> p.getFileName().toString().endsWith(".java.txt"))
                    .sorted()
                    .toList();
        }
    }

    private static void checkFixture(String name, String text, Path dir) throws Exception {
        Map<Integer, String> wants = parseWants(text);
        List<Finding> findings = Javalint.analyze(name, text, parseReporters(text, dir));

        for (Finding f : findings) {
            String want = wants.get(f.line());
            assertNotNull(want,
                    name + ": unexpected " + f.rule() + " at line " + f.line() + ": " + f.message());
            assertTrue(Pattern.compile(want).matcher(f.message()).find(),
                    name + ": line " + f.line() + " message [" + f.message() + "] does not match /" + want + "/");
        }
        List<Integer> fired = findings.stream().map(Finding::line).toList();
        for (Map.Entry<Integer, String> e : wants.entrySet()) {
            assertTrue(fired.contains(e.getKey()),
                    name + ": expected a finding matching /" + e.getValue()
                            + "/ at line " + e.getKey() + ", none fired");
        }
    }

    /** A `// reporters: <file>#<Class.method>,...` directive names sibling
     *  testdata files as declared reporters; each is read from the testdata
     *  directory and resolved to its package, exactly as the CLI reads the
     *  repo-relative files a real `.tackbox/reporters` names. */
    private static List<Reporters.Resolved> parseReporters(String text, Path dir) throws Exception {
        for (String line : text.split("\n", -1)) {
            Matcher m = REPORTERS.matcher(line);
            if (m.find()) {
                List<Reporters.Resolved> out = new ArrayList<>();
                for (Reporters.Declared d : Reporters.parse(m.group(1).trim())) {
                    out.add(Reporters.resolve(d, Files.readString(dir.resolve(d.file()))));
                }
                return out;
            }
        }
        return List.of();
    }

    private static Map<Integer, String> parseWants(String text) {
        Map<Integer, String> wants = new LinkedHashMap<>();
        String[] lines = text.split("\n", -1);
        for (int i = 0; i < lines.length; i++) {
            Matcher m = WANT.matcher(lines[i]);
            if (m.find()) {
                wants.put(i + 1, m.group(1));
            }
        }
        return wants;
    }
}

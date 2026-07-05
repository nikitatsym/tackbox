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

    @TestFactory
    List<DynamicTest> fixtures() throws Exception {
        List<DynamicTest> tests = new ArrayList<>();
        for (Path fixture : fixtureFiles()) {
            String name = fixture.getFileName().toString();
            String text = Files.readString(fixture);
            tests.add(DynamicTest.dynamicTest(name, () -> checkFixture(name, text)));
        }
        assertTrue(!tests.isEmpty(), "no testdata fixtures found on classpath");
        return tests;
    }

    private static List<Path> fixtureFiles() throws Exception {
        URL url = WantHarnessTest.class.getResource("/testdata");
        assertNotNull(url, "testdata resource directory missing");
        Path dir = Path.of(url.toURI());
        try (Stream<Path> s = Files.list(dir)) {
            return s.filter(p -> p.getFileName().toString().endsWith(".java.txt"))
                    .sorted()
                    .toList();
        }
    }

    private static void checkFixture(String name, String text) {
        Map<Integer, String> wants = parseWants(text);
        List<Finding> findings = Javalint.analyze(name, text);

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

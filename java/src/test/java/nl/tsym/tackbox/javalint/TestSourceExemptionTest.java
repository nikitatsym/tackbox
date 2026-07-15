package nl.tsym.tackbox.javalint;

import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;

/** D-4: the reporter-arg / notify-gate rules (JV009, JV010) skip test sources
 *  under src/test/ - a dynamic dedupKey that fires in production is clean there,
 *  parity with Go _test.go and JS/Python. The swallow rule still runs. */
class TestSourceExemptionTest {

    private static final String SRC =
            "import nl.tsym.tackbox.report.Report;\n"
            + "class Foo {\n"
            + "  void h(String key) {\n"
            + "    try { risky(); } catch (java.io.IOException e) {\n"
            + "      Report.error(\"disk write failed\", e, null, key);\n"
            + "    }\n"
            + "  }\n"
            + "  void risky() throws Exception {}\n"
            + "}\n";

    @Test
    void dynamicDedupFiresInProductionButNotInTestSource() {
        List<Finding> prod = Javalint.analyze("src/main/java/Foo.java", SRC, List.of());
        List<Finding> test = Javalint.analyze("src/test/java/Foo.java", SRC, List.of());
        assertTrue(prod.stream().anyMatch(f -> f.rule().equals("JV010")),
                "a dynamic dedupKey must fire JV010 in a production source");
        assertTrue(test.stream().noneMatch(f -> f.rule().equals("JV010")),
                "JV010 must skip a src/test/ source");
    }
}

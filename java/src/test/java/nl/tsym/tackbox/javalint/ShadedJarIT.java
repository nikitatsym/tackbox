package nl.tsym.tackbox.javalint;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonElement;
import com.google.gson.JsonParser;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.zip.ZipFile;
import org.junit.jupiter.api.Test;

class ShadedJarIT {

    @Test
    void packagedJarRunsPmdWithoutXpathDependencies() throws Exception {
        Path java = Path.of(System.getProperty("java.home"), "bin", "java");
        Process process = new ProcessBuilder(java.toString(), "-jar", "target/javalint.jar",
                "src/test/resources/testdata/UnusedBindings.java.txt",
                "src/test/resources/testdata/UnusedBindingsConventions.java.txt")
                .redirectErrorStream(true).start();
        String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        assertEquals(0, process.waitFor(), output);
        for (String id : new String[] {"JV011", "JV012", "JV013", "JV014"}) {
            assertTrue(output.contains("\"" + id + "\": ["), output);
        }
        assertFalse(output.contains("UnusedBindingsConventions.java.txt"), output);
        assertFalse(output.contains("Exception"), output);
        try (ZipFile jar = new ZipFile("target/javalint.jar")) {
            assertTrue(jar.getEntry("META-INF/third-party/inventory.json") != null);
            assertTrue(jar.getEntry("META-INF/third-party/pmd.LICENSE.txt") != null);
            String inventory = Files.readString(Path.of("../engines/licenses/javalint/inventory.json"));
            assertEquals(inventory, new String(jar.getInputStream(
                    jar.getEntry("META-INF/third-party/inventory.json")).readAllBytes(), StandardCharsets.UTF_8));
            assertTrue(jar.getEntry("META-INF/third-party/nice-xml-messages.LICENSE.txt") != null);
            assertTrue(jar.getEntry("META-INF/third-party/pcollections.LICENSE.txt") != null);
            for (JsonElement component : JsonParser.parseString(inventory).getAsJsonObject()
                    .getAsJsonArray("components")) {
                String license = component.getAsJsonObject().get("license_path").getAsString();
                assertTrue(jar.getEntry("META-INF/third-party/" + license) != null, license);
            }
            assertTrue(jar.getEntry("META-INF/services/net.sourceforge.pmd.lang.Language") != null);
        }
    }
}

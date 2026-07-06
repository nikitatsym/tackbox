package nl.tsym.tackbox.javalint;

import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/** Serializes findings into erclint's `-json` shape so the python CLI parses
 *  javalint and erclint through one path:
 *  { "<file>": { "<rule>": [ {"posn","end","message"}, ... ] } }.
 *  Files and rules are sorted for a deterministic, diffable dump. */
final class JsonWriter {

    private JsonWriter() {}

    static String write(List<Finding> findings) {
        Map<String, Map<String, List<Finding>>> byFile = new TreeMap<>();
        for (Finding f : findings) {
            byFile.computeIfAbsent(normalizeSeparators(f.file()), k -> new TreeMap<>())
                  .computeIfAbsent(f.rule(), k -> new java.util.ArrayList<>())
                  .add(f);
        }

        StringBuilder sb = new StringBuilder();
        sb.append("{");
        boolean firstFile = true;
        for (var fileEntry : byFile.entrySet()) {
            if (!firstFile) {
                sb.append(",");
            }
            firstFile = false;
            sb.append("\n  ").append(quote(fileEntry.getKey())).append(": {");
            boolean firstRule = true;
            for (var ruleEntry : fileEntry.getValue().entrySet()) {
                if (!firstRule) {
                    sb.append(",");
                }
                firstRule = false;
                sb.append("\n    ").append(quote(ruleEntry.getKey())).append(": [");
                boolean firstF = true;
                for (Finding f : ruleEntry.getValue()) {
                    if (!firstF) {
                        sb.append(",");
                    }
                    firstF = false;
                    sb.append("\n      {\"posn\": ").append(quote(normalizeSeparators(f.posn())))
                      .append(", \"end\": ").append(quote(normalizeSeparators(f.end())))
                      .append(", \"message\": ").append(quote(f.message()))
                      .append("}");
                }
                sb.append("\n    ]");
            }
            sb.append("\n  }");
        }
        sb.append("\n}\n");
        return sb.toString();
    }

    // Repo-relative keys must match git's separator on every OS.
    private static String normalizeSeparators(String path) {
        return path.replace('\\', '/');
    }

    private static String quote(String s) {
        StringBuilder sb = new StringBuilder(s.length() + 2);
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                default -> {
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
                }
            }
        }
        sb.append('"');
        return sb.toString();
    }
}

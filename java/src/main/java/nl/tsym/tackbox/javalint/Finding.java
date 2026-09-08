package nl.tsym.tackbox.javalint;

/** One reported diagnostic. Positions are 1-based line/column, mirroring
 *  JavaParser and the go/analysis posn format erclint emits. */
public record Finding(
        String rule,
        String file,
        int line,
        int column,
        int endLine,
        int endColumn,
        String message,
        Marker marker) {

    public Finding(String rule, String file, int line, int column, int endLine, int endColumn, String message) {
        this(rule, file, line, column, endLine, endColumn, message, null);
    }

    public boolean suppressed() {
        return marker != null;
    }

    /** `file:line:col`, the erclint posn shape the python CLI parses. */
    public String posn() {
        return file + ":" + line + ":" + column;
    }

    public String end() {
        return file + ":" + endLine + ":" + endColumn;
    }
}

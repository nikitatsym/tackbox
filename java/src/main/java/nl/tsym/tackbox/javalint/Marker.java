package nl.tsym.tackbox.javalint;

/** A suppression marker parsed from a `// <kind>: <reason>` line comment.
 *  Port of go/internal/markers: the kinds and the non-empty-reason rule are
 *  the same across languages so authors learn one idiom. */
public record Marker(Kind kind, String reason) {

    public enum Kind {
        NO_REPORT("no-report:"),
        PARSE_SKIP("parse-skip:"),
        NIL_RETURN("nil-return:");

        final String prefix;

        Kind(String prefix) {
            this.prefix = prefix;
        }
    }

    /** The marker kind a comment's content is shaped as, reason or no reason,
     *  or null for a plain comment. Lets the index surface marker-shaped
     *  comments that suppress nothing (trailing, empty reason) instead of
     *  ignoring them silently. */
    public static Kind kindOf(String content) {
        String text = content.strip();
        for (Kind kind : Kind.values()) {
            if (text.startsWith(kind.prefix)) {
                return kind;
            }
        }
        return null;
    }

    /** Parse a line comment's content (the text after `//`). Returns null when
     *  it is not a marker or carries an empty reason - an empty reason never
     *  suppresses, exactly as in the go index and the opengrep java rule. */
    public static Marker parse(String content) {
        String text = content.strip();
        for (Kind kind : Kind.values()) {
            if (text.startsWith(kind.prefix)) {
                String reason = text.substring(kind.prefix.length()).strip();
                return reason.isEmpty() ? null : new Marker(kind, reason);
            }
        }
        return null;
    }
}

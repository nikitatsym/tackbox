package nl.tsym.tackbox.javalint;

/** A suppression marker parsed from a `// <kind>: <reason>` line comment.
 *  Port of go/internal/markers: the kinds and the minimum-reason rule are the
 *  same across languages so authors learn one idiom. */
public record Marker(Kind kind, String reason) {

    /** D009: a reason must be at least this many chars after trimming.
     *  Non-empty was too cheap (`ok` / `todo` passed). */
    public static final int MIN_REASON = 10;

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
     *  it is not a marker or carries a reason under MIN_REASON chars - a too-short
     *  reason never suppresses (D009), the same floor as the go/js/py parsers. */
    public static Marker parse(String content) {
        String text = content.strip();
        for (Kind kind : Kind.values()) {
            if (text.startsWith(kind.prefix)) {
                String reason = text.substring(kind.prefix.length()).strip();
                return reason.length() < MIN_REASON ? null : new Marker(kind, reason);
            }
        }
        return null;
    }
}

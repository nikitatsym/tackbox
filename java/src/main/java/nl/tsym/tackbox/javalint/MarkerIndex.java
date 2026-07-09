package nl.tsym.tackbox.javalint;

import com.github.javaparser.JavaToken;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.comments.Comment;
import com.github.javaparser.ast.comments.LineComment;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;

/** Port of go/internal/markers.Index: markers live on any line of the comment
 *  block directly above a node. Only `//` line comments are recognized; the
 *  marker nearest the block's end (closest to the node below) wins. Marker-shaped
 *  comments that suppress nothing - trailing code, or carrying an empty reason -
 *  are collected as dead so a firing rule can say why they did not count. */
public final class MarkerIndex {

    /** Why a marker-shaped comment suppresses nothing. */
    public enum Cause { TRAILING, EMPTY_REASON }

    /** A marker-shaped comment that suppresses nothing. */
    public record Dead(int line, Marker.Kind kind, Cause cause) {}

    /** A run of line comments on consecutive lines. */
    private static final class Group {
        final int lastLine;
        final Marker marker; // nearest-the-end marker, or null

        Group(int lastLine, Marker marker) {
            this.lastLine = lastLine;
            this.marker = marker;
        }
    }

    private final List<Group> groups = new ArrayList<>();
    private final List<Dead> dead = new ArrayList<>();

    public MarkerIndex(CompilationUnit cu) {
        List<LineComment> comments = new ArrayList<>();
        for (Comment c : cu.getAllContainedComments()) {
            if (!(c instanceof LineComment lc) || lc.getRange().isEmpty()) {
                continue;
            }
            boolean standalone = isStandalone(lc);
            if (standalone) {
                comments.add(lc);
            }
            Marker.Kind kind = Marker.kindOf(lc.getContent());
            if (kind == null) {
                continue;
            }
            int line = lc.getRange().get().begin.line;
            if (!standalone) {
                dead.add(new Dead(line, kind, Cause.TRAILING));
            } else if (Marker.parse(lc.getContent()) == null) {
                dead.add(new Dead(line, kind, Cause.EMPTY_REASON));
            }
        }
        dead.sort(Comparator.comparingInt(Dead::line));
        comments.sort(Comparator.comparingInt(c -> c.getRange().get().begin.line));

        int groupLast = -1;
        Marker groupMarker = null;
        int prevEnd = -2;
        for (LineComment c : comments) {
            int begin = c.getRange().get().begin.line;
            int end = c.getRange().get().end.line;
            if (begin > prevEnd + 1) {
                flush(groupLast, groupMarker);
                groupMarker = null;
            }
            Marker m = Marker.parse(c.getContent());
            if (m != null) {
                groupMarker = m; // later comment wins: nearest the node
            }
            groupLast = end;
            prevEnd = end;
        }
        flush(groupLast, groupMarker);
    }

    private void flush(int lastLine, Marker marker) {
        if (lastLine >= 0) {
            groups.add(new Group(lastLine, marker));
        }
    }

    /** Marker-shaped comments that suppress nothing, in line order. */
    public List<Dead> dead() {
        return dead;
    }

    /** The marker carried by the comment block directly above `line`, or null. */
    public Marker above(int line) {
        for (Group g : groups) {
            if (g.lastLine == line - 1 && g.marker != null) {
                return g.marker;
            }
        }
        return null;
    }

    /** True iff only whitespace precedes the comment on its own line: a
     *  comment trailing code must never join or start a standalone block,
     *  so it stays invisible to grouping and to marker lookup. */
    private static boolean isStandalone(LineComment c) {
        Optional<JavaToken> tok = c.getTokenRange().map(tr -> tr.getBegin());
        Optional<JavaToken> prev = tok.flatMap(JavaToken::getPreviousToken);
        while (prev.isPresent() && prev.get().getCategory() == JavaToken.Category.WHITESPACE_NO_EOL) {
            prev = prev.get().getPreviousToken();
        }
        return prev.isEmpty() || prev.get().getCategory() == JavaToken.Category.EOL;
    }
}

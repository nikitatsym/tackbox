package nl.tsym.tackbox.javalint;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.comments.Comment;
import com.github.javaparser.ast.comments.LineComment;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/** Port of go/internal/markers.Index: markers live on any line of the comment
 *  block directly above a node. Only `//` line comments are recognized; the
 *  marker nearest the block's end (closest to the node below) wins. */
public final class MarkerIndex {

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

    public MarkerIndex(CompilationUnit cu) {
        List<LineComment> comments = new ArrayList<>();
        for (Comment c : cu.getAllContainedComments()) {
            if (c instanceof LineComment lc && lc.getRange().isPresent()) {
                comments.add(lc);
            }
        }
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

    /** The marker carried by the comment block directly above `line`, or null. */
    public Marker above(int line) {
        for (Group g : groups) {
            if (g.lastLine == line - 1 && g.marker != null) {
                return g.marker;
            }
        }
        return null;
    }
}

package nl.tsym.tackbox.javalint.rules;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.stmt.CatchClause;
import java.util.ArrayList;
import java.util.List;
import nl.tsym.tackbox.javalint.Finding;
import nl.tsym.tackbox.javalint.MarkerIndex;
import nl.tsym.tackbox.javalint.Recognition;

/** Base for Recognition-backed rules that judge every catch clause: holds the
 *  ctor and the findAll(CatchClause) walk; subclasses add findings per catch. */
abstract class CatchRule {

    protected final Recognition rec;

    CatchRule(Recognition rec) {
        this.rec = rec;
    }

    public final List<Finding> check(String file, CompilationUnit cu, MarkerIndex markers) {
        List<Finding> out = new ArrayList<>();
        for (CatchClause cc : cu.findAll(CatchClause.class)) {
            check(file, cu, markers, cc, out);
        }
        return out;
    }

    abstract void check(String file, CompilationUnit cu, MarkerIndex markers, CatchClause cc, List<Finding> out);
}

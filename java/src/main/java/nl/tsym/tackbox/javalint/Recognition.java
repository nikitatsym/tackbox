package nl.tsym.tackbox.javalint;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.Parameter;
import com.github.javaparser.ast.body.TypeDeclaration;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.FieldAccessExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.ThisExpr;
import com.github.javaparser.ast.expr.VariableDeclarationExpr;
import com.github.javaparser.ast.nodeTypes.NodeWithParameters;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.ExpressionStmt;
import com.github.javaparser.ast.stmt.Statement;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.ast.type.Type;
import java.util.List;
import java.util.Optional;

/** Recognizes the ways a catch may legitimately answer its error - the Java port
 *  of the go recognition model, done entirely import-origin (javalint is
 *  source-only; it never loads the consumer's compiled classpath):
 *
 *  - tier-1: error/warn on a receiver whose declared type resolves to
 *    org.slf4j.Logger. The gate is the declared type's origin (explicit import
 *    or fully-qualified name), never the receiver name or the method-owner name.
 *  - tier-2: a call whose (package, Class, method) matches a `.tackbox-reporters`
 *    declaration. The declaration's package comes from parsing the declared file;
 *    the call-site's comes from resolving the qualifier through this file's
 *    imports and package - the same origin machinery as tier-1.
 *  - printing terminal: System.err.println/printf carrying the caught, or
 *    printStackTrace on the caught - stderr output is visible, not silence.
 *
 *  Both captures require argument-flow (the caught must reach the call);
 *  printStackTrace is the exception, where the caught IS the receiver. A
 *  qualifier declared in this same file, or resolvable only through a wildcard
 *  import, fails closed. */
public final class Recognition {

    private static final String SLF4J_PACKAGE = "org.slf4j";
    private static final String SLF4J_LOGGER = "Logger";

    /** A resolved call target: the package and class its qualifier denotes. */
    private record Origin(String packageName, String className) {}

    private final List<Reporters.Resolved> reporters;

    public Recognition(List<Reporters.Resolved> reporters) {
        this.reporters = reporters;
    }

    /** A call that reports or prints the caught, so a catch reaching it is not
     *  silent. `caught` is the enclosing catch parameter's name. */
    public boolean capturesOrPrints(CompilationUnit cu, MethodCallExpr call, String caught) {
        return isPrintingTerminal(call, caught)
                || slf4jCaptures(cu, call, caught)
                || declaredCaptures(cu, call, caught);
    }

    // --- tier-1: slf4j ------------------------------------------------------

    private boolean slf4jCaptures(CompilationUnit cu, MethodCallExpr call, String caught) {
        String m = call.getNameAsString();
        if ((!m.equals("error") && !m.equals("warn")) || !argFlows(call, caught)) {
            return false;
        }
        Origin o = callOrigin(cu, call).orElse(null);
        return o != null && o.packageName().equals(SLF4J_PACKAGE) && o.className().equals(SLF4J_LOGGER);
    }

    // --- tier-2: declared reporters -----------------------------------------

    private boolean declaredCaptures(CompilationUnit cu, MethodCallExpr call, String caught) {
        if (reporters.isEmpty() || !argFlows(call, caught)) {
            return false;
        }
        Origin o = callOrigin(cu, call).orElse(null);
        if (o == null) {
            return false;
        }
        String method = call.getNameAsString();
        for (Reporters.Resolved r : reporters) {
            if (r.method().equals(method)
                    && r.className().equals(o.className())
                    && r.packageName().equals(o.packageName())) {
                return true;
            }
        }
        return false;
    }

    // --- origin resolution (shared by both tiers) ---------------------------

    /** The (package, Class) a call's qualifier denotes, resolved source-only.
     *  A variable receiver resolves through its declared type; a bare or
     *  fully-qualified type qualifier resolves directly. Empty when the origin
     *  cannot be established (an expression receiver, an unqualified call, or a
     *  same-file / wildcard-only qualifier) - fail closed. */
    private Optional<Origin> callOrigin(CompilationUnit cu, MethodCallExpr call) {
        Expression scope = call.getScope().orElse(null);
        if (scope instanceof NameExpr ne) {
            Optional<Type> varType = declaredTypeOf(ne.getNameAsString(), call);
            if (varType.isPresent()) {
                return typeOrigin(cu, varType.get());
            }
            return packageOfSimple(cu, ne.getNameAsString())
                    .map(pkg -> new Origin(pkg, ne.getNameAsString()));
        }
        if (scope instanceof FieldAccessExpr fa) {
            if (fa.getScope() instanceof ThisExpr) {
                return declaredTypeOf(fa.getNameAsString(), call).flatMap(t -> typeOrigin(cu, t));
            }
            return Optional.of(new Origin(fa.getScope().toString(), fa.getNameAsString()));
        }
        return Optional.empty();
    }

    /** The origin of a declared type as written: a fully-qualified name yields
     *  its package directly; a simple name is resolved through imports/package. */
    private static Optional<Origin> typeOrigin(CompilationUnit cu, Type t) {
        if (!(t instanceof ClassOrInterfaceType cit)) {
            return Optional.empty();
        }
        String simple = cit.getNameAsString();
        if (cit.getScope().isPresent()) {
            return Optional.of(new Origin(cit.getScope().get().asString(), simple));
        }
        return packageOfSimple(cu, simple).map(pkg -> new Origin(pkg, simple));
    }

    /** The package a simple type name resolves to source-only: empty if a type
     *  of that name is declared in this file (a local shadow - never the
     *  external library, and never a cross-file declared reporter); the import's
     *  package for an explicit single-type import; otherwise this file's own
     *  package (a same-package reference). A wildcard-only name has no explicit
     *  origin, so it falls to the same-package package and thus never matches a
     *  differently-packaged target - fail closed. */
    private static Optional<String> packageOfSimple(CompilationUnit cu, String simple) {
        if (declaresType(cu, simple)) {
            return Optional.empty();
        }
        for (ImportDeclaration imp : cu.getImports()) {
            if (!imp.isAsterisk() && !imp.isStatic() && lastSegment(imp.getNameAsString()).equals(simple)) {
                return Optional.of(packageBefore(imp.getNameAsString()));
            }
        }
        return Optional.of(cu.getPackageDeclaration().map(pd -> pd.getNameAsString()).orElse(""));
    }

    private static boolean declaresType(CompilationUnit cu, String simple) {
        return cu.findAll(TypeDeclaration.class).stream()
                .anyMatch(td -> td.getNameAsString().equals(simple));
    }

    // --- printing terminals -------------------------------------------------

    private boolean isPrintingTerminal(MethodCallExpr call, String caught) {
        Expression scope = call.getScope().orElse(null);
        String m = call.getNameAsString();
        if (m.equals("printStackTrace")) {
            return scope instanceof NameExpr ne && ne.getNameAsString().equals(caught);
        }
        if ((m.equals("println") || m.equals("printf")) && isSystemErr(scope)) {
            return argFlows(call, caught);
        }
        return false;
    }

    private static boolean isSystemErr(Expression scope) {
        return scope instanceof FieldAccessExpr fa
                && fa.getNameAsString().equals("err")
                && fa.getScope() instanceof NameExpr ne
                && ne.getNameAsString().equals("System");
    }

    // --- receiver / argument primitives -------------------------------------

    /** The caught identifier appears anywhere in the call's arguments. */
    public boolean argFlows(MethodCallExpr call, String caught) {
        if (caught == null) {
            return false;
        }
        for (Expression arg : call.getArguments()) {
            if (arg.findFirst(NameExpr.class, ne -> ne.getNameAsString().equals(caught)).isPresent()) {
                return true;
            }
        }
        return false;
    }

    /** The declared type of `name` as written, by lexical scope walk: an
     *  enclosing callable's parameter, a local variable, or an enclosing type's
     *  field. Empty when no declaration lives in this file. */
    private static Optional<Type> declaredTypeOf(String name, Node use) {
        for (Node cur = use; cur != null; cur = cur.getParentNode().orElse(null)) {
            if (cur instanceof NodeWithParameters<?> np) {
                for (Parameter p : np.getParameters()) {
                    if (p.getNameAsString().equals(name)) {
                        return Optional.of(p.getType());
                    }
                }
            }
            if (cur instanceof BlockStmt block) {
                for (Statement st : block.getStatements()) {
                    Optional<Type> t = localVarType(st, name);
                    if (t.isPresent()) {
                        return t;
                    }
                }
            }
            if (cur instanceof TypeDeclaration<?> td) {
                for (FieldDeclaration fd : td.getFields()) {
                    for (VariableDeclarator v : fd.getVariables()) {
                        if (v.getNameAsString().equals(name)) {
                            return Optional.of(v.getType());
                        }
                    }
                }
            }
        }
        return Optional.empty();
    }

    private static Optional<Type> localVarType(Statement st, String name) {
        if (st instanceof ExpressionStmt es && es.getExpression() instanceof VariableDeclarationExpr vde) {
            for (VariableDeclarator v : vde.getVariables()) {
                if (v.getNameAsString().equals(name)) {
                    return Optional.of(v.getType());
                }
            }
        }
        return Optional.empty();
    }

    private static String lastSegment(String dotted) {
        int i = dotted.lastIndexOf('.');
        return i < 0 ? dotted : dotted.substring(i + 1);
    }

    private static String packageBefore(String qualified) {
        int i = qualified.lastIndexOf('.');
        return i < 0 ? "" : qualified.substring(0, i);
    }
}

package nl.tsym.tackbox.javalint;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.body.Parameter;
import com.github.javaparser.ast.body.TypeDeclaration;
import com.github.javaparser.ast.body.VariableDeclarator;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.FieldAccessExpr;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.ObjectCreationExpr;
import com.github.javaparser.ast.expr.ThisExpr;
import com.github.javaparser.ast.expr.VariableDeclarationExpr;
import com.github.javaparser.ast.nodeTypes.NodeWithParameters;
import com.github.javaparser.ast.stmt.BlockStmt;
import com.github.javaparser.ast.stmt.ExpressionStmt;
import com.github.javaparser.ast.stmt.Statement;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.ast.type.Type;
import java.util.ArrayList;
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

    /** A resolved call target: the package and class its qualifier denotes.
     *  tier1Eligible is false only for a bare FQN-shaped dotted qualifier
     *  (package.Class.method()) - slf4j error/warn are instance methods, so a
     *  static-shaped call can never be a real capture, only a tier-2 candidate. */
    private record Origin(String packageName, String className, boolean tier1Eligible) {
        Origin(String packageName, String className) {
            this(packageName, className, true);
        }
    }

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

    /** A tier-1 / tier-2 capture of the caught, excluding printing terminals: the
     *  ways that hand the error to a reporting backend (slf4j error/warn, a
     *  declared reporter), which an upstream handler would count a second time.
     *  A stderr print is visible but not backend-reported, so it is not here. */
    public boolean captures(CompilationUnit cu, MethodCallExpr call, String caught) {
        return slf4jCaptures(cu, call, caught) || declaredCaptures(cu, call, caught);
    }

    // --- tier-1: slf4j ------------------------------------------------------

    private boolean slf4jCaptures(CompilationUnit cu, MethodCallExpr call, String caught) {
        String m = call.getNameAsString();
        if ((!m.equals("error") && !m.equals("warn")) || !argFlows(call, caught)) {
            return false;
        }
        Origin o = callOrigin(cu, call).orElse(null);
        return o != null && o.tier1Eligible()
                && o.packageName().equals(SLF4J_PACKAGE) && o.className().equals(SLF4J_LOGGER);
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
     *  fully-qualified type qualifier resolves directly; an unqualified call
     *  resolves to the enclosing type or an explicit static import (see
     *  unqualifiedOrigin). Empty when the origin cannot be established (an
     *  expression receiver, or a same-file / wildcard-only qualifier) - fail
     *  closed. */
    private Optional<Origin> callOrigin(CompilationUnit cu, MethodCallExpr call) {
        Expression scope = call.getScope().orElse(null);
        if (scope == null) {
            return unqualifiedOrigin(cu, call);
        }
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
            return dottedQualifierOrigin(cu, fa);
        }
        return Optional.empty();
    }

    /** An unqualified call `m(...)`: resolved to a method the enclosing type
     *  declares (implicit this, origin = this file's package + that type), which
     *  shadows any import; otherwise to the owner of an explicit
     *  `import static pkg.Class.m`. A wildcard static import cannot name the
     *  owner source-only, so it fails closed - as the wildcard type import does. */
    private Optional<Origin> unqualifiedOrigin(CompilationUnit cu, MethodCallExpr call) {
        String m = call.getNameAsString();
        Optional<Origin> enclosing = enclosingTypeOrigin(cu, call, m);
        return enclosing.isPresent() ? enclosing : staticImportOrigin(cu, m);
    }

    /** The nearest enclosing type that declares a method named `m`, as (this
     *  file's package, that type's name). Once the walk crosses a static-nested,
     *  local, or anonymous type boundary, implicit-this no longer carries an
     *  enclosing instance - a match beyond that point is credited only if it is
     *  itself static, matching javac's own rule. Empty when no reachable
     *  enclosing type declares `m`. */
    private static Optional<Origin> enclosingTypeOrigin(CompilationUnit cu, Node use, String m) {
        boolean crossedStatic = false;
        for (Node cur = use; cur != null; cur = cur.getParentNode().orElse(null)) {
            if (cur instanceof TypeDeclaration<?> td) {
                Optional<MethodDeclaration> match = td.getMethods().stream()
                        .filter(md -> md.getNameAsString().equals(m))
                        .findFirst();
                if (match.isPresent()) {
                    if (crossedStatic && !match.get().isStatic()) {
                        return Optional.empty();
                    }
                    String pkg = cu.getPackageDeclaration().map(pd -> pd.getNameAsString()).orElse("");
                    return Optional.of(new Origin(pkg, td.getNameAsString()));
                }
                crossedStatic = crossedStatic || crossesStaticBoundary(td);
            } else if (cur instanceof ObjectCreationExpr oce && oce.getAnonymousClassBody().isPresent()) {
                crossedStatic = true;
            }
        }
        return Optional.empty();
    }

    /** A static-nested type, or a local type (conservatively treated the same
     *  as static, since resolving whether its enclosing method is itself
     *  static/instance is out of scope here - fail closed). */
    private static boolean crossesStaticBoundary(TypeDeclaration<?> td) {
        return td.isStatic() || (td instanceof ClassOrInterfaceDeclaration cid && cid.isLocalClassDeclaration());
    }

    /** The (package, Class) owner of an explicit `import static pkg.Class.m`, or
     *  empty when no such import names `m` (wildcard static imports resolve no
     *  owner source-only). Static-shaped, so it is a tier-2 candidate only. */
    private static Optional<Origin> staticImportOrigin(CompilationUnit cu, String m) {
        for (ImportDeclaration imp : cu.getImports()) {
            if (imp.isStatic() && !imp.isAsterisk() && lastSegment(imp.getNameAsString()).equals(m)) {
                String pkgClass = packageBefore(imp.getNameAsString());
                return Optional.of(new Origin(packageBefore(pkgClass), lastSegment(pkgClass), false));
            }
        }
        return Optional.empty();
    }

    /** A dotted qualifier `S...N` before `.method(...)`, resolved source-only -
     *  never the raw AST text. An FQN-shaped chain (lowercase segments then an
     *  uppercase Class) is a tier-2-only candidate, gated by the same-file-reject
     *  pin; a bare `Holder.FIELD` head that resolves to a type declared in this
     *  file resolves the field's declared type through the normal type-origin
     *  machinery; anything else - a cross-file class-qualified field, a chained
     *  expression - fails closed. */
    private Optional<Origin> dottedQualifierOrigin(CompilationUnit cu, FieldAccessExpr fa) {
        List<String> parts = dottedSegments(fa).orElse(null);
        if (parts == null) {
            return Optional.empty();
        }
        if (isFqnShape(parts)) {
            String pkg = String.join(".", parts.subList(0, parts.size() - 1));
            String cls = parts.get(parts.size() - 1);
            if (declaresTopLevelType(cu, cls, pkg)) {
                return Optional.empty();
            }
            return Optional.of(new Origin(pkg, cls, false));
        }
        if (fa.getScope() instanceof NameExpr head) {
            return holderFieldOrigin(cu, head.getNameAsString(), fa.getNameAsString());
        }
        return Optional.empty();
    }

    /** The dotted identifier chain `a.b...Z` a FieldAccessExpr denotes, head
     *  first. Empty if any link is not a plain name (a chained expression). */
    private static Optional<List<String>> dottedSegments(Expression e) {
        List<String> segments = new ArrayList<>();
        Expression cur = e;
        while (cur instanceof FieldAccessExpr f) {
            segments.add(0, f.getNameAsString());
            cur = f.getScope();
        }
        if (cur instanceof NameExpr ne) {
            segments.add(0, ne.getNameAsString());
            return Optional.of(segments);
        }
        return Optional.empty();
    }

    /** Java package.Class convention: every segment but the last starts
     *  lowercase, the last starts uppercase. */
    private static boolean isFqnShape(List<String> segments) {
        if (segments.size() < 2) {
            return false;
        }
        for (int i = 0; i < segments.size() - 1; i++) {
            if (!startsWithCase(segments.get(i), false)) {
                return false;
            }
        }
        return startsWithCase(segments.get(segments.size() - 1), true);
    }

    private static boolean startsWithCase(String s, boolean upper) {
        if (s.isEmpty()) {
            return false;
        }
        char c = s.charAt(0);
        return upper ? Character.isUpperCase(c) : Character.isLowerCase(c);
    }

    /** The same-file-reject pin: a tier-2 FQN candidate never matches if this
     *  very file declares a top-level type of that name in that package - an
     *  in-file decoy must not be able to impersonate a cross-file target. */
    private static boolean declaresTopLevelType(CompilationUnit cu, String className, String pkg) {
        String ownPkg = cu.getPackageDeclaration().map(pd -> pd.getNameAsString()).orElse("");
        return ownPkg.equals(pkg) && cu.getTypes().stream().anyMatch(td -> td.getNameAsString().equals(className));
    }

    /** Rule 2: Holder.FIELD where Holder is a type declared in this file -
     *  resolve FIELD's declared type inside Holder's body and run the normal
     *  type-origin machinery on it. Empty if Holder or the field is not found. */
    private static Optional<Origin> holderFieldOrigin(CompilationUnit cu, String holderName, String fieldName) {
        return cu.findAll(TypeDeclaration.class).stream()
                .filter(td -> td.getNameAsString().equals(holderName))
                .findFirst()
                .flatMap(holder -> fieldTypeIn(holder, fieldName))
                .flatMap(t -> typeOrigin(cu, t));
    }

    private static Optional<Type> fieldTypeIn(TypeDeclaration<?> td, String fieldName) {
        for (FieldDeclaration fd : td.getFields()) {
            for (VariableDeclarator v : fd.getVariables()) {
                if (v.getNameAsString().equals(fieldName)) {
                    return Optional.of(v.getType());
                }
            }
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

"""File-local import-origin resolution for the tackbox_report capture verbs.

Derived from bandit (PyCQA), Apache-2.0: the import alias map
(visit_Import / visit_ImportFrom) and the dotted-attribute chain resolution
(get_call_name / _get_attr_qual_name). See https://github.com/PyCQA/bandit
(tag 1.9.4). The ordered kill/shadow layer below is ours, per DECISIONS D010.
"""

from __future__ import annotations

import ast

_PACKAGE = "tackbox_report"

# The five recognized verbs. report_error/report_warn/report_quiet/report_panic
# are captures; notify is user-lane-only. This resolver only reports which verb
# a call site resolves to - the checker splits capture from notify.
VERBS = frozenset(
    {"report_error", "report_warn", "report_quiet", "report_panic", "notify"}
)

# state[name] is a verb string (name bound to that verb) or _MODULE (name bound
# to the tackbox_report module, reached by attribute). _MODULE is not in VERBS,
# so a bare-name call through it never resolves.
_MODULE = "<module:tackbox_report>"

_TRY_TYPES: tuple[type, ...] = (ast.Try,) + (
    (ast.TryStar,) if hasattr(ast, "TryStar") else ()
)


def resolve_map(tree: ast.AST, owner: bool = False) -> dict[int, str | None]:
    """`id(ast.Call) -> verb name or None` for every call in `tree`.

    A call resolves to a verb only through the file's own tackbox_report import
    bindings (D010). `owner` credits the package's own top-level verb defs as the
    origin (the tackbox_report package self-credits, D010)."""
    return _Resolver(owner).build(tree)


# --- name extraction (no scope entry) -------------------------------------


def _arg_names(args: ast.arguments) -> list[str]:
    names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    if args.vararg:
        names.append(args.vararg.arg)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return names


def _arg_defaults(args: ast.arguments):
    yield from args.defaults
    for d in args.kw_defaults:
        if d is not None:
            yield d


def _target_names(target: ast.expr) -> list[str]:
    """Names bound by an assignment / for / with / del target (any ctx),
    flattening tuple/list/starred targets. Attribute/subscript bind no name."""
    out: list[str] = []
    stack = [target]
    while stack:
        t = stack.pop()
        if isinstance(t, ast.Name):
            out.append(t.id)
        elif isinstance(t, ast.Starred):
            stack.append(t.value)
        elif isinstance(t, (ast.Tuple, ast.List)):
            stack.extend(t.elts)
    return out


def _pattern_names(pattern: ast.pattern) -> list[str]:
    out: list[str] = []
    for n in ast.walk(pattern):
        if isinstance(n, ast.MatchAs) and n.name:
            out.append(n.name)
        elif isinstance(n, ast.MatchStar) and n.name:
            out.append(n.name)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            out.append(n.rest)
    return out


def _enclosing_exec_parts(node: ast.AST):
    """Sub-expressions of a def/class/lambda that execute in the ENCLOSING scope
    (decorators, argument defaults, class bases/keywords): their calls resolve
    with the enclosing state, not the new scope's."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        yield from node.decorator_list
        yield from _arg_defaults(node.args)
    elif isinstance(node, ast.Lambda):
        yield from _arg_defaults(node.args)
    elif isinstance(node, ast.ClassDef):
        yield from node.decorator_list
        yield from node.bases
        for kw in node.keywords:
            yield kw.value


def _survey(roots) -> tuple[list[ast.Call], list[ast.AST]]:
    """(calls, scopes) lexically direct in `roots`: descend statements and
    expressions, stop at nested def/class/lambda (collected as scopes, their
    enclosing-exec parts surveyed for calls, their bodies left for dispatch)."""
    calls: list[ast.Call] = []
    scopes: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            scopes.append(node)
            for part in _enclosing_exec_parts(node):
                visit(part)
            return
        if isinstance(node, ast.Call):
            calls.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for r in roots:
        visit(r)
    return calls, scopes


def _collect_shadows(stmts: list[ast.stmt]) -> set[str]:
    """Names locally bound (store/del/def/class/import/except/match/walrus) in
    `stmts`, not descending into nested function/lambda/class bodies. An
    over-approximate kill set - the conservative direction (D010)."""
    names: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(child.name)
                for part in _enclosing_exec_parts(child):
                    visit(part)
                continue
            if isinstance(child, ast.Lambda):
                for part in _enclosing_exec_parts(child):
                    visit(part)
                continue
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for alias in child.names:
                    if alias.name != "*":
                        names.add(alias.asname or alias.name.split(".")[0])
                continue
            if isinstance(child, ast.ExceptHandler):
                if child.name:
                    names.add(child.name)
                visit(child)
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                names.add(child.id)
            elif isinstance(child, ast.MatchAs) and child.name:
                names.add(child.name)
            elif isinstance(child, ast.MatchStar) and child.name:
                names.add(child.name)
            elif isinstance(child, ast.MatchMapping) and child.rest:
                names.add(child.rest)
            visit(child)

    for s in stmts:
        visit(s)
    return names


def _walrus_targets(expr: ast.expr) -> list[str]:
    out: list[str] = []

    def visit(n: ast.AST) -> None:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
            out.append(n.target.id)
        for c in ast.iter_child_nodes(n):
            visit(c)

    visit(expr)
    return out


def _minus(visible: dict[str, str], names: set[str]) -> dict[str, str]:
    return {k: v for k, v in visible.items() if k not in names}


def _resolve(call: ast.Call, visible: dict[str, str]) -> str | None:
    """The verb a call resolves to under `visible`, else None. Name form
    (`report_error(...)`) and single-level attribute form (`rep.report_error(...)`
    through an `import tackbox_report [as rep]`) both resolve."""
    f = call.func
    if isinstance(f, ast.Name):
        b = visible.get(f.id)
        return b if b in VERBS else None
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        if visible.get(f.value.id) == _MODULE and f.attr in VERBS:
            return f.attr
    return None


def _apply_import(node: ast.AST, state: dict[str, str]) -> None:
    """Bind or kill names for one import (bandit's alias-map recipe, restricted
    to the fixed tackbox_report origin). A same-name import from elsewhere kills
    any tracked binding; `import *` from tackbox_report binds all five verbs, from
    any other module binds nothing (D010)."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.asname:
                if alias.name == _PACKAGE:
                    state[alias.asname] = _MODULE
                else:
                    state.pop(alias.asname, None)
            else:
                top = alias.name.split(".")[0]
                if top == _PACKAGE:
                    state[_PACKAGE] = _MODULE
                else:
                    state.pop(top, None)
        return
    if not isinstance(node, ast.ImportFrom):
        return
    if node.level and node.level > 0:  # relative import: not our top-level package
        for alias in node.names:
            if alias.name != "*":
                state.pop(alias.asname or alias.name, None)
        return
    if node.module == _PACKAGE:
        for alias in node.names:
            if alias.name == "*":
                for v in VERBS:
                    state[v] = v
            elif alias.name in VERBS:
                state[alias.asname or alias.name] = alias.name
            else:
                state.pop(alias.asname or alias.name, None)
    else:
        for alias in node.names:
            if alias.name != "*":
                state.pop(alias.asname or alias.name, None)


class _Resolver:
    def __init__(self, owner: bool):
        self.owner = owner
        self.resolution: dict[int, str | None] = {}

    def build(self, tree: ast.AST) -> dict[int, str | None]:
        state: dict[str, str] = {}
        for stmt in getattr(tree, "body", []):
            self._exec(stmt, state)  # module-level: at-position, mutates -> module_final
        _, scopes = _survey(getattr(tree, "body", []))
        for sc in scopes:
            self._dispatch(sc, state)  # functions/classes run against module_final
        return self.resolution

    # -- module-level flow-sensitive scan ----------------------------------

    def _scan(self, stmts: list[ast.stmt], state: dict[str, str]) -> None:
        for stmt in stmts:
            self._exec(stmt, state)

    def _exec(self, stmt: ast.stmt, state: dict[str, str]) -> None:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            _apply_import(stmt, state)
            return
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._resolve_parts(_enclosing_exec_parts(stmt), state)
            self._bind_def(stmt.name, state)
            return
        if isinstance(stmt, ast.ClassDef):
            self._resolve_parts(_enclosing_exec_parts(stmt), state)
            state.pop(stmt.name, None)
            return
        if isinstance(stmt, ast.If):
            self._resolve_here(stmt.test, state)
            self._scan(stmt.body, state)
            self._scan(stmt.orelse, state)
            return
        if isinstance(stmt, (ast.For, ast.AsyncFor)):
            self._resolve_here(stmt.iter, state)
            self._kill(_target_names(stmt.target), state)
            self._scan(stmt.body, state)
            self._scan(stmt.orelse, state)
            return
        if isinstance(stmt, ast.While):
            self._resolve_here(stmt.test, state)
            self._scan(stmt.body, state)
            self._scan(stmt.orelse, state)
            return
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                self._resolve_here(item.context_expr, state)
                if item.optional_vars is not None:
                    self._kill(_target_names(item.optional_vars), state)
            self._scan(stmt.body, state)
            return
        if isinstance(stmt, _TRY_TYPES):
            self._scan(stmt.body, state)
            for h in stmt.handlers:
                if h.type is not None:
                    self._resolve_here(h.type, state)
                if h.name:
                    state.pop(h.name, None)
                self._scan(h.body, state)
            self._scan(stmt.orelse, state)
            self._scan(stmt.finalbody, state)
            return
        if isinstance(stmt, ast.Assign):
            self._resolve_here(stmt.value, state)
            for tgt in stmt.targets:
                self._kill(_target_names(tgt), state)
            return
        if isinstance(stmt, ast.AnnAssign):
            self._resolve_here(stmt.value, state)
            self._kill(_target_names(stmt.target), state)
            return
        if isinstance(stmt, ast.AugAssign):
            self._resolve_here(stmt.value, state)
            self._kill(_target_names(stmt.target), state)
            return
        if isinstance(stmt, ast.Delete):
            for tgt in stmt.targets:
                self._kill(_target_names(tgt), state)
            return
        if isinstance(stmt, ast.Match):
            self._resolve_here(stmt.subject, state)
            for case in stmt.cases:
                self._kill(_pattern_names(case.pattern), state)
                self._resolve_here(case.guard, state)
                self._scan(case.body, state)
            return
        # simple statement (Expr / Return / Raise / Assert / ...): resolve its
        # direct expression children at the current state.
        for e in ast.iter_child_nodes(stmt):
            if isinstance(e, ast.expr):
                self._resolve_here(e, state)

    def _bind_def(self, name: str, state: dict[str, str]) -> None:
        # Owner package: a top-level verb def IS the origin (self-credit, D010).
        if self.owner and name in VERBS:
            state[name] = name
        else:
            state.pop(name, None)

    def _kill(self, names, state: dict[str, str]) -> None:
        for nm in names:
            state.pop(nm, None)

    def _resolve_parts(self, parts, state: dict[str, str]) -> None:
        for p in parts:
            self._resolve_here(p, state)

    def _resolve_here(self, expr: ast.expr | None, state: dict[str, str]) -> None:
        if expr is None:
            return
        calls, _ = _survey([expr])
        for c in calls:
            self.resolution[id(c)] = _resolve(c, state)
        self._kill(_walrus_targets(expr), state)

    # -- nested-scope dispatch (against module_final) ----------------------

    def _dispatch(self, scope: ast.AST, visible: dict[str, str]) -> None:
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            local = set(_arg_names(scope.args)) | _collect_shadows(scope.body)
            inner = _minus(visible, local)
            self._emit(scope.body, inner, inner)
        elif isinstance(scope, ast.Lambda):
            inner = _minus(visible, set(_arg_names(scope.args)))
            self._emit([scope.body], inner, inner)
        elif isinstance(scope, ast.ClassDef):
            # Direct class-body calls see the class scope; methods and nested
            # classes see the enclosing (module) scope, never class locals.
            direct = _minus(visible, _collect_shadows(scope.body))
            self._emit(scope.body, direct, visible)

    def _emit(self, roots, call_visible: dict[str, str], child_visible: dict[str, str]) -> None:
        calls, scopes = _survey(roots)
        for c in calls:
            self.resolution[id(c)] = _resolve(c, call_visible)
        for sc in scopes:
            self._dispatch(sc, child_visible)

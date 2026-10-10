"""Check file-local references to module-private names, not reachability (D021)."""

from __future__ import annotations

import ast

from .origin import enclosing_exec_parts, target_names

_TYPE_ALIAS = getattr(ast, "TypeAlias", ())
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _annotations(node: ast.AST):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if arg.annotation is not None:
                yield arg.annotation
        for arg in (node.args.vararg, node.args.kwarg):
            if arg is not None and arg.annotation is not None:
                yield arg.annotation
        if node.returns is not None:
            yield node.returns
    for parameter in getattr(node, "type_params", ()):
        yield parameter


def _literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_literal(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is not None and _literal(key) and _literal(value)
                   for key, value in zip(node.keys, node.values))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return isinstance(node.operand, ast.Constant)
    return False


def _class_effect(node: ast.ClassDef) -> bool:
    if node.decorator_list or node.keywords or any(
        not isinstance(base, ast.Name) or base.id != "object" for base in node.bases
    ):
        return True

    def effect(part: ast.AST) -> bool:
        if isinstance(part, ast.ClassDef):
            return _class_effect(part)
        if isinstance(part, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if getattr(part, "decorator_list", ()):
                return True
            return any(effect(expr) for expr in (*enclosing_exec_parts(part), *_annotations(part)))
        if isinstance(part, (ast.Call, ast.Global, ast.Nonlocal)):
            return True
        if isinstance(part, (ast.Assign, ast.AnnAssign)):
            if part.value is not None and not _literal(part.value):
                return True
        if isinstance(part, (ast.Attribute, ast.Subscript)) and isinstance(part.ctx, (ast.Store, ast.Del)):
            return True
        return any(effect(child) for child in ast.iter_child_nodes(part))

    return any(effect(part) for part in node.body)


def _bindings(tree: ast.AST) -> tuple[dict[str, ast.AST], set[int]]:
    bindings: dict[str, ast.AST] = {}
    declarations: set[int] = set()

    def add(name: str, node: ast.AST, discard: bool = False) -> None:
        if name.startswith("_") and not (name.startswith("__") and name.endswith("__")):
            if name != "_" or not discard:
                bindings.setdefault(name, node)
                declarations.add(id(node))

    def descend(node: ast.AST) -> None:
        if isinstance(node, _DEFS):
            add(node.name, node)
            return
        if isinstance(node, _TYPE_ALIAS):
            add(node.name.id, node)
            return
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    for name in target_names(target):
                        add(name, node, discard=True)
        elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
            add(node.target.id, node, discard=True)
        for child in ast.iter_child_nodes(node):
            descend(child)

    descend(tree)
    return bindings, declarations


class _Uses(ast.NodeVisitor):
    def __init__(self, declarations: set[int], aliases: dict[str, str]):
        self.declarations = declarations
        self.aliases = aliases
        self.used: set[str] = set()
        self.blocked: set[str] = set()
        self.forward = False
        self.values = False
        self.module_scope = True

    def credit(self, name: str) -> None:
        if name not in self.blocked:
            self.used.add(name)

    def spelling(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    def type_visit(self, node: ast.AST, values: bool = False) -> None:
        forward, previous_values = self.forward, self.values
        self.forward = True
        self.values |= values
        self.visit(node)
        self.forward, self.values = forward, previous_values

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Load, ast.Del)):
            self.credit(node.id)

    def visit_JoinedStr(self, node):
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                self.visit(value.value)
                if value.format_spec is not None:
                    self.visit(value.format_spec)

    def visit_Constant(self, node):
        if self.forward and not self.values and isinstance(node.value, str):
            try:
                parsed = ast.parse(node.value, mode="eval")
            except (SyntaxError, ValueError):
                # no-report: invalid forward-reference strings are values, not type references
                return
            self.visit(parsed)

    def visit_Subscript(self, node):
        self.visit(node.value)
        if isinstance(node.value, ast.Call) and self.spelling(node.value.func) in {"globals", "vars"}:
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                self.credit(node.slice.value)
        head = self.spelling(node.value)
        elements = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        for index, element in enumerate(elements):
            self.type_visit(element, values=head == "Literal" or (head == "Annotated" and index > 0))

    def visit_Call(self, node):
        forward = self.forward
        self.forward = False
        name = self.spelling(node.func)
        if name in {"getattr", "hasattr", "setattr", "delattr"} and len(node.args) > 1:
            literal = node.args[1]
            if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                self.credit(literal.value)
        self.visit(node.func)
        for index, arg in enumerate(node.args):
            if (name == "cast" and index == 0) or (name == "TypeVar" and index > 0):
                self.type_visit(arg)
            else:
                self.visit(arg)
        for kw in node.keywords:
            if name == "TypeVar" and kw.arg == "bound":
                self.type_visit(kw.value)
            else:
                self.visit(kw.value)
        self.forward = forward

    def all_exports(self, value):
        if isinstance(value, (ast.List, ast.Tuple)):
            for element in value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    self.credit(element.value)
        elif isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
            self.all_exports(value.left)
            self.all_exports(value.right)

    def visit_Assign(self, node):
        if self.module_scope and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            self.all_exports(node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self.type_visit(node.annotation)
        if node.value is not None:
            if self.module_scope and isinstance(node.target, ast.Name) and node.target.id == "__all__":
                self.all_exports(node.value)
            if self.spelling(node.annotation) == "TypeAlias":
                self.type_visit(node.value)
            else:
                self.visit(node.value)
        self.visit(node.target)

    def visit_AugAssign(self, node):
        if isinstance(node.target, ast.Name):
            self.credit(node.target.id)
            if self.module_scope and node.target.id == "__all__":
                self.all_exports(node.value)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.definition(node)

    def visit_AsyncFunctionDef(self, node):
        self.definition(node)

    def visit_ClassDef(self, node):
        self.definition(node)

    def definition(self, node):
        if id(node) in self.declarations and (node.decorator_list or isinstance(node, ast.ClassDef) and _class_effect(node)):
            self.credit(node.name)
        for part in enclosing_exec_parts(node):
            self.visit(part)
        for annotation in _annotations(node):
            self.type_visit(annotation)
        blocked, scope = self.blocked, self.module_scope
        if id(node) in self.declarations:
            self.blocked = self.blocked | {node.name}
        self.module_scope = False
        for part in node.body:
            self.visit(part)
        self.blocked, self.module_scope = blocked, scope

    def visit_TypeAlias(self, node):
        for parameter in node.type_params:
            self.type_visit(parameter)
        blocked = self.blocked
        if id(node) in self.declarations:
            self.blocked = self.blocked | {node.name.id}
        self.type_visit(node.value)
        self.blocked = blocked


def findings(tree: ast.AST):
    bindings, declarations = _bindings(tree)
    aliases = {alias.asname or alias.name: alias.name
               for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
               for alias in node.names}
    uses = _Uses(declarations, aliases)
    uses.visit(tree)
    for name, node in bindings.items():
        if name not in uses.used:
            message = (
                f"`{name}` is not used in this module; delete it while preserving any "
                "assignment or definition-time effects, or remove its leading underscore "
                "if another production module uses it"
            )
            yield node.lineno, node.col_offset, "TBX012", message, None, None

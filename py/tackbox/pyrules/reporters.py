"""Tier-2 `.tackbox/reporters` resolution for the python engine.

Tier-1 verbs resolve by import origin (see origin.py); tier-2 declarations stay
name-based. Unlike Go/JS/Java, which resolve a call's callee back to the
declaring file, the flake8/ast layer has no cross-module type info: a declared
`file#func` is validated to have a module-level `def` OR a top-level import
binding of that name in the file (a facade re-exporting a helper verb, D010; a
dead symbol is a hard error, exit 2, scope-independent), but recognition at call
sites is by the declared NAME - any same-named call, from any module, counts,
and only when the caught error flows into its arguments.
"""

from __future__ import annotations

import ast
from pathlib import Path


def resolve_declared(
    specs: list[tuple[str, str]],
) -> tuple[frozenset[str], tuple[str, str] | None]:
    """Validate every `(file, func)` declaration, scope-independent.

    Returns (reporter names, None) when all resolve, or (empty, (file, func))
    for the first declaration whose function has no module-level def - the
    caller turns that dead symbol into a hard exit. Returning rather than
    raising keeps the caller free of an except handler of its own.
    """
    names: set[str] = set()
    for file, func in specs:
        if not _has_top_level_def(file, func):
            return frozenset(), (file, func)
        names.add(func)
    return frozenset(names), None


def _has_top_level_def(file: str, func: str) -> bool:
    """A declared reporter resolves iff the file has a top-level `def`/`async def`
    OR a top-level import binding of the name (a facade re-exporting a helper verb,
    D010): `from x import func`, `from x import y as func`, `import x as func`.
    Plain assignments stay invalid."""
    tree = ast.parse(Path(file).read_text(encoding="utf-8"), filename=file)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            return True
        if isinstance(node, ast.ImportFrom):
            if any((alias.asname or alias.name) == func for alias in node.names):
                return True
        if isinstance(node, ast.Import):
            # `import x as func` binds func; `import x.y` binds the top name `x`.
            if any((alias.asname or alias.name.split(".")[0]) == func for alias in node.names):
                return True
    return False


def arg_flows(call: ast.Call, err_name: str | None) -> bool:
    """True iff `err_name` appears anywhere in the call's argument subtrees.

    The argument-flow primitive (Go ContainsIdent / JS walk): a declared sink
    captures only when the caught error reaches it. Positional and keyword
    arguments both count; an empty err_name (no `as E`) never flows.
    """
    if not err_name:
        return False
    subtrees = list(call.args) + [kw.value for kw in call.keywords]
    for arg in subtrees:
        for node in ast.walk(arg):
            if isinstance(node, ast.Name) and node.id == err_name:
                return True
    return False

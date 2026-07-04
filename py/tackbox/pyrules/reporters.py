"""Tier-2 `.tackbox-reporters` resolution for the python engine.

Ports the Go/JS contract the opengrep tier could not: resolve each declared
`file#func` to a module-level `def` in that file, treat a dead symbol as a
hard error (exit 2, scope-independent), and count a call to the declared name
as a capture only when the caught error flows into the call's arguments.
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
    tree = ast.parse(Path(file).read_text(encoding="utf-8"), filename=file)
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func
        for node in tree.body
    )


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

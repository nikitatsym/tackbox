"""The flake8 plugin: AST visitor for the TBX python rules.

The seven exception rules are ported from exceptions-python.yaml (opengrep) to
a py-native engine so the two things opengrep could not express become
expressible: the sound supervised-shutdown carve-out and tier-2 reporter symbol
validation. Findings carry the pre-migration rule id in the message so parity
stays id-for-id. TBX008 (python-test-skip) is py-native from the start.
"""

from __future__ import annotations

import ast
import sys

from tackbox import __version__ as _version

from .codes import CODE_TO_ID, MESSAGES
from .markers import MarkerIndex, TEST_SKIP
from . import reporters as _reporters

# Sound supervised-shutdown carve-out: an except catching only these, whose
# body kills/terminates the child, is a clean shutdown - not a swallow.
_SHUTDOWN_TYPES = frozenset(
    {"TimeoutExpired", "subprocess.TimeoutExpired", "ProcessLookupError"}
)
_SHUTDOWN_CALLS = frozenset({"kill", "terminate"})
_EXIT_CALLS = frozenset({"sys.exit", "os._exit"})

# Built-in tier-1 reporters: the tackbox_report public capture API, recognized by
# NAME (pyrules has no import origin). So a consumer's
# `except X as e: report_error(..., e)` is credited without a `# no-report:`
# marker or a `.tackbox-reporters` entry - the Python analog of how Go credits
# go/report by origin and JS credits tackbox/report (DECISIONS D004). Name-model
# limitation: a same-named function from any module is credited too; origin is
# not provable source-only.
_BUILTIN_REPORTERS = frozenset({"report_error", "report_warn", "report_quiet", "report_panic"})


def _dotted_name(expr: ast.expr) -> str:
    """`Name`/`Attribute` chain as a dotted string, else ""."""
    parts: list[str] = []
    cur: ast.expr = expr
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""


def _iter_body(handler: ast.ExceptHandler):
    """Every node under the handler's body, not descending into nested
    function/lambda scopes (their statements are not this handler's)."""
    for stmt in handler.body:
        yield from _walk(stmt)


def _walk(node: ast.AST):
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield from _walk(child)


def _handler_raises(handler: ast.ExceptHandler) -> bool:
    return any(isinstance(n, ast.Raise) for n in _iter_body(handler))


def _is_bare_or_base(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    return _dotted_name(handler.type) == "BaseException"


def _handler_types(handler: ast.ExceptHandler) -> list[str]:
    t = handler.type
    if t is None:
        return []
    elems = t.elts if isinstance(t, ast.Tuple) else [t]
    return [_dotted_name(e) for e in elems]


def _is_shutdown_carveout(handler: ast.ExceptHandler) -> bool:
    types = _handler_types(handler)
    if not types or not all(t in _SHUTDOWN_TYPES for t in types):
        return False
    for n in _iter_body(handler):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in _SHUTDOWN_CALLS
        ):
            return True
    return False


def _reporter_captures(handler: ast.ExceptHandler, reporter_names: frozenset[str]) -> bool:
    """A recognized reporter (built-in tier-1 or declared tier-2) is called in the
    handler with the caught error flowing into it - the name-based capture path."""
    if not reporter_names or not handler.name:
        return False
    for n in _iter_body(handler):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id in reporter_names
            and _reporters.arg_flows(n, handler.name)
        ):
            return True
    return False


def _exit_call(handler: ast.ExceptHandler) -> ast.Call | None:
    for n in _iter_body(handler):
        if isinstance(n, ast.Call) and _dotted_name(n.func) in _EXIT_CALLS:
            return n
    return None


def _is_useless(handler: ast.ExceptHandler) -> bool:
    """Body is exactly `raise` (bare) or `raise <caught-name>` - a no-op wrapper."""
    if len(handler.body) != 1 or not isinstance(handler.body[0], ast.Raise):
        return False
    r = handler.body[0]
    if r.exc is None:
        return True
    return (
        handler.name is not None
        and isinstance(r.exc, ast.Name)
        and r.exc.id == handler.name
    )


def _reraise_without_cause(handler: ast.ExceptHandler) -> ast.Raise | None:
    """`raise NewError(...)` (a call) without `from` inside `except ... as E`."""
    if handler.name is None:
        return None
    for n in _iter_body(handler):
        if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call) and n.cause is None:
            return n
    return None


def _is_suppress_call(call: ast.Call) -> bool:
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr == "suppress":
        return isinstance(f.value, ast.Name) and f.value.id == "contextlib"
    return isinstance(f, ast.Name) and f.id == "suppress"


def _suppress_allowlisted(call: ast.Call) -> bool:
    # asyncio.CancelledError is the cancel handshake, not an error to log.
    return len(call.args) == 1 and _dotted_name(call.args[0]) == "asyncio.CancelledError"


def _chain_ends(dotted: str, suffix: str) -> bool:
    """Origin-aware suffix match: `pytest.mark.skip` and `mark.skip` (from `from
    pytest import mark`) both end in `mark.skip`."""
    return dotted == suffix or dotted.endswith("." + suffix)


def _kw_value(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _reason_expr_ok(expr: ast.expr) -> bool:
    """A non-literal reason (variable, f-string) is trusted; only an empty or
    whitespace-only string literal fails."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value.strip() != ""
    return True


def _reason_present(call: ast.Call | None, positional: bool, kw: bool) -> bool:
    """True iff an acceptable reason is given via the allowed source(s). A bare
    decorator (no call) or an absent/empty reason -> False."""
    if call is None:
        return False
    expr: ast.expr | None = None
    if positional and call.args:
        expr = call.args[0]
    if expr is None and kw:
        expr = _kw_value(call, "reason")
    if expr is None:
        return False
    return _reason_expr_ok(expr)


def _skip_decorator_flag(dec: ast.expr, has_unittest_skip: bool) -> bool:
    """True iff `dec` is a skip/xfail decorator whose reason is missing/empty.
    Bare-name `@skip` counts only when `from unittest import skip` is in the
    file (origin gate)."""
    call = dec if isinstance(dec, ast.Call) else None
    dotted = _dotted_name(call.func if call else dec)
    if _chain_ends(dotted, "mark.skip"):
        return not _reason_present(call, positional=True, kw=True)
    if _chain_ends(dotted, "mark.skipif"):
        return not _reason_present(call, positional=False, kw=True)
    if _chain_ends(dotted, "mark.xfail"):
        return not _reason_present(call, positional=False, kw=True)
    if _chain_ends(dotted, "unittest.skip"):
        return not _reason_present(call, positional=True, kw=False)
    if dotted == "skip" and has_unittest_skip:
        return not _reason_present(call, positional=True, kw=False)
    return False


def _is_pytest_skip_call(call: ast.Call) -> bool:
    return _chain_ends(_dotted_name(call.func), "pytest.skip")


def _imports_unittest_skip(tree: ast.AST) -> bool:
    """True iff the file binds the bare name `skip` to unittest's skip; an
    aliased import (`skip as s`) binds a different name and does not gate."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "unittest":
            for alias in node.names:
                if alias.name == "skip" and alias.asname in (None, "skip"):
                    return True
    return False


class _Visitor(ast.NodeVisitor):
    def __init__(
        self,
        markers: MarkerIndex,
        skip_markers: MarkerIndex,
        reporter_names: frozenset[str],
        unittest_skip: bool,
    ):
        self.markers = markers
        self.skip_markers = skip_markers
        # Built-in tier-1 names are always recognized; declared tier-2 names extend them.
        self.reporter_names = _BUILTIN_REPORTERS | reporter_names
        self.unittest_skip = unittest_skip
        self.findings: list[tuple[int, int, str]] = []
        self._func_depth = 0

    def _add(self, node: ast.AST, code: str) -> None:
        self.findings.append((node.lineno, node.col_offset, code))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_skip_decorators(node)
        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._check_skip_decorators(node)
        self.generic_visit(node)

    def _check_skip_decorators(self, node: ast.AST) -> None:
        # Marker anchors to the flagged decorator's own line: `suppresses` is
        # line-above, and the flagged decorator (not the first) is the natural
        # anchor - the marker sits next to the construct it excuses.
        for dec in node.decorator_list:
            if not _skip_decorator_flag(dec, self.unittest_skip):
                continue
            if not self.skip_markers.suppresses(dec.lineno):
                self._add(dec, "TBX008")

    def visit_Import(self, node: ast.Import) -> None:
        if self._func_depth > 0:
            self._add(node, "TBX006")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._func_depth > 0:
            self._add(node, "TBX006")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _is_suppress_call(node) and not _suppress_allowlisted(node):
            self._add(node, "TBX002")
        if _is_pytest_skip_call(node) and not _reason_present(node, positional=True, kw=True):
            if not self.skip_markers.suppresses(node.lineno):
                self._add(node, "TBX008")
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        for handler in node.handlers:
            self._check_handler(node, handler)
        self.generic_visit(node)

    def _check_handler(self, try_node: ast.Try, handler: ast.ExceptHandler) -> None:
        if _is_bare_or_base(handler):
            self._add(handler, "TBX003")
            return

        reraise = _reraise_without_cause(handler)
        if reraise is not None:
            self._add(reraise, "TBX004")

        if _is_useless(handler):
            self._add(handler, "TBX005")

        exit_call = _exit_call(handler)
        if exit_call is not None:
            self._add(exit_call, "TBX007")

        if not _handler_raises(handler) and self._swallows(try_node, handler):
            self._add(try_node, "TBX001")

    def _swallows(self, try_node: ast.Try, handler: ast.ExceptHandler) -> bool:
        if _is_shutdown_carveout(handler):
            return False
        if self.markers.suppresses(handler.body[0].lineno):
            return False
        if _reporter_captures(handler, self.reporter_names):
            return False
        return True


class Plugin:
    """flake8 AST plugin. Invoked only in closed form:
    `flake8 --isolated --disable-noqa --select=TBX [--reporters=...] <files>`."""

    name = "tackbox-pyrules"
    version = _version

    _reporter_names: frozenset[str] = frozenset()

    def __init__(self, tree, filename, file_tokens):
        self.tree = tree
        self.filename = filename
        self.file_tokens = file_tokens

    def run(self):
        visitor = _Visitor(
            MarkerIndex(self.file_tokens),
            MarkerIndex(self.file_tokens, prefix=TEST_SKIP),
            type(self)._reporter_names,
            _imports_unittest_skip(self.tree),
        )
        visitor.visit(self.tree)
        for line, col, code in visitor.findings:
            text = f"{code} {CODE_TO_ID[code]}: {MESSAGES[code]}"
            yield line, col, text, type(self)

    @classmethod
    def add_options(cls, option_manager) -> None:
        option_manager.add_option(
            "--reporters",
            parse_from_config=False,
            default="",
            help="declared reporter sinks as file#func,... (.tackbox-reporters)",
        )

    @classmethod
    def parse_options(cls, options) -> None:
        specs = _parse_reporter_specs(options.reporters)
        names, dead = _reporters.resolve_declared(specs)
        if dead is not None:
            # Hard error, scope-independent: parity with go/js reporter validation.
            file, func = dead
            sys.stderr.write(f".tackbox-reporters: no top-level function {func} in {file}\n")
            raise SystemExit(2)
        cls._reporter_names = names


def _parse_reporter_specs(raw: str) -> list[tuple[str, str]]:
    """`file#func,file2#func2` -> [(file, func)]; only .py declarations (the CLI
    self-filters, but split on the last `#` so a `#` in a path is tolerated)."""
    specs: list[tuple[str, str]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        hash_i = entry.rfind("#")
        if hash_i <= 0:
            continue
        file, func = entry[:hash_i], entry[hash_i + 1:]
        if file.endswith(".py"):
            specs.append((file, func))
    return specs

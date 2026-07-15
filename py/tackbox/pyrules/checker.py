"""The flake8 plugin: AST visitor for the TBX python rules.

The seven exception rules are ported from exceptions-python.yaml (opengrep) to
a py-native engine so the two things opengrep could not express become
expressible: the sound supervised-shutdown carve-out and tier-2 reporter symbol
validation. Findings carry the pre-migration rule id in the message so parity
stays id-for-id. TBX008 (python-test-skip) is py-native from the start.
"""

from __future__ import annotations

import ast
import re
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

# notify is the user-lane-only verb (D006): recognized by name like the
# reporters, but NEVER a capture - it credits a failure path for TBX001 without
# joining _BUILTIN_REPORTERS, and TBX010 gates it. panic is excluded from the
# double-lane capture set (it is terminal, like Go's capPanic).
_NOTIFY_NAME = "notify"
_LANE_CAPTURE_BUILTINS = _BUILTIN_REPORTERS - {"report_panic"}

# Broad except types for the notify gate (D006). bare / BaseException single
# catches early-return to TBX003, so Exception is the broad type this gate
# actually sees; the set also covers a tuple member that is broad.
_BROAD_EXCEPT_TYPES = frozenset(
    {"Exception", "BaseException", "builtins.Exception", "builtins.BaseException"}
)

# User-lane verbs whose msg must be a static literal (D007) and whose dedup_key
# must be a well-formed literal (D008), recognized by name (the D004 caveat).
# report_panic is exempt (it takes a name, not a msg/dedup_key).
_MSG_VERBS = frozenset({"report_error", "report_warn", _NOTIFY_NAME})
_DEDUP_VERBS = frozenset({"report_error", "report_warn", "report_quiet", _NOTIFY_NAME})
_DEDUP_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_-]*(:[a-zA-Z0-9_.-]+)?$")

# TBX010 double-lane and TBX011 dedup_key sub-messages (the default arm of each
# code lives in codes.MESSAGES; these carry the other arms as finding details).
_DOUBLE_LANE_MSG = (
    "except both captures and notifies on one path; report_error/report_warn already reach the "
    "user lane, so the notify double-shows - drop the notify, or use only notify with no capture"
)
_DEDUP_MISSING = "dedup_key is required on a user-lane verb - it is the Sentry fingerprint and coalescing key"
_DEDUP_NOT_LITERAL = "dedup_key must be a static string literal so the fingerprint is stable"
_DEDUP_BAD_FORMAT = "dedup_key must match area.suffix[:identifier]"


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


def _irrefutable(case: ast.match_case) -> bool:
    """A guardless capture-all case (`case _` / `case x`): it always matches, so
    a match carrying one is exhaustive - no implicit no-match fall-through."""
    return case.guard is None and isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None


# _silent_path verdicts: a statement (or statement list) either terminates every
# path handled (_TERMINAL), drops the error on some path (_BAD), or falls through
# carrying a bool - whether an event has routed the error on the way (reported).
_TERMINAL = "terminal"
_BAD = "bad"

# The opaque, order-blind statements: their internal control flow is not modeled,
# so a routing event or raise anywhere inside credits every path through them.
_OPAQUE_STMTS: tuple[type, ...] = (ast.Try, ast.For, ast.While, ast.AsyncFor) + (
    (ast.TryStar,) if hasattr(ast, "TryStar") else ()
)


def _silent_path(body: list[ast.stmt], reporter_names: frozenset[str], caught: str | None) -> bool:
    """Some execution path through the handler body drops the caught error:
    reaches a plain return / break / continue / sys.exit, or the body's end,
    with no capture or notify routing it on the way. Path-sensitive - if/elif/
    else and match cases are exclusive legs, so a handled leg does not credit
    its silent complement; a raise terminates the path handled; with is
    transparent; try and loops stay opaque, lenient units. Ported from the JS
    makeHandledAnalysis / Java SilentScan references."""

    def credits(node: ast.AST) -> bool:
        # An event routing the caught error: a recognized capture or a notify.
        if not caught:
            return False
        for n in _walk(node):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and (n.func.id in reporter_names or n.func.id == _NOTIFY_NAME)
                and _reporters.arg_flows(n, caught)
            ):
                return True
        return False

    def opaque_handled(node: ast.AST) -> bool:
        return credits(node) or any(isinstance(n, ast.Raise) for n in _walk(node))

    def merge(legs: list[str | bool]) -> str | bool:
        if any(v == _BAD for v in legs):
            return _BAD
        if all(v == _TERMINAL for v in legs):
            return _TERMINAL
        return all(v == _TERMINAL or v for v in legs)  # falls through reported iff every leg does

    def analyze(st: ast.stmt, reported: bool) -> str | bool:
        if isinstance(st, ast.Raise):
            return _TERMINAL
        if isinstance(st, ast.Return):
            return _TERMINAL if reported or (st.value is not None and credits(st.value)) else _BAD
        if isinstance(st, (ast.Break, ast.Continue)):
            return _TERMINAL if reported else _BAD
        if isinstance(st, ast.If):
            then_v = analyze_list(st.body, reported)
            else_v = analyze_list(st.orelse, reported) if st.orelse else reported
            return merge([then_v, else_v])
        if isinstance(st, ast.Match):
            legs = [analyze_list(c.body, reported) for c in st.cases]
            if not any(_irrefutable(c) for c in st.cases):
                legs.append(reported)  # a no-match fall-through reaches the body's end
            return merge(legs)
        if isinstance(st, (ast.With, ast.AsyncWith)):
            return analyze_list(st.body, reported)
        if isinstance(st, _OPAQUE_STMTS):
            return True if opaque_handled(st) else reported
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) and _dotted_name(st.value.func) in _EXIT_CALLS:
            return _TERMINAL if reported else _BAD  # sys.exit / os._exit drops the error unless already routed
        return True if credits(st) else reported

    def analyze_list(stmts: list[ast.stmt], reported: bool) -> str | bool:
        for st in stmts:
            v = analyze(st, reported)
            if v == _BAD or v == _TERMINAL:
                return v
            reported = v
        return reported

    r = analyze_list(body, False)
    if r == _BAD:
        return True
    if r == _TERMINAL:
        return False
    return not r  # fell off the end: silent iff no event routed the error


def _notifies(handler: ast.ExceptHandler) -> bool:
    """A notify(...) carrying the caught error is called in the handler - it
    routes the error to the user lane, terminating that path (D006). Never a
    capture; TBX010 decides whether the except type is narrow enough."""
    if not handler.name:
        return False
    for n in _iter_body(handler):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == _NOTIFY_NAME
            and _reporters.arg_flows(n, handler.name)
        ):
            return True
    return False


def _is_broad_except(handler: ast.ExceptHandler) -> bool:
    """True iff any caught type is broad (Exception/BaseException). A tuple is
    narrow only when every member is narrow."""
    types = _handler_types(handler)
    if not types:
        return True  # bare - the caller has already emitted TBX003
    return any(t in _BROAD_EXCEPT_TYPES for t in types)


def _lane_conflict(body: list[ast.stmt], capture_names: frozenset[str], caught: str | None) -> bool:
    """Some execution path through body both captures (a recognized reporter,
    panic excluded) and notifies (a notify carrying the caught) - the D006
    double-lane. Path-sensitive: exclusive if/else and match-case legs do not
    pair, nor does a capture after a notify+return. if/elif/else and match cases
    are followed; loops / try / with stay opaque (their calls may-run), matching
    the flat leniency the other Python rules already take. Each live path carries
    which lanes have fired; the state set holds at most four tuples."""
    found = False

    def calls(node: ast.AST) -> tuple[bool, bool]:
        cap = notify = False
        for n in _walk(node):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)):
                continue
            if not _reporters.arg_flows(n, caught):
                continue
            if n.func.id in capture_names:
                cap = True
            if n.func.id == _NOTIFY_NAME:
                notify = True
        return cap, notify

    def apply(states: list[tuple[bool, bool]], cap: bool, notify: bool) -> list[tuple[bool, bool]]:
        nonlocal found
        if not cap and not notify:
            return states
        out: list[tuple[bool, bool]] = []
        for c, nt in states:
            ns = (c or cap, nt or notify)
            if ns[0] and ns[1]:
                found = True
            if ns not in out:
                out.append(ns)
        return out

    def dedup(states: list[tuple[bool, bool]]) -> list[tuple[bool, bool]]:
        out: list[tuple[bool, bool]] = []
        for s in states:
            if s not in out:
                out.append(s)
        return out

    def step(st: ast.stmt, states: list[tuple[bool, bool]]) -> list[tuple[bool, bool]]:
        if isinstance(st, ast.If):
            base = apply(states, *calls(st.test))
            then_exit = step_list(st.body, base)
            else_exit = step_list(st.orelse, base) if st.orelse else base
            return dedup(then_exit + else_exit)
        if isinstance(st, ast.Match):
            # Each case is an exclusive leg (only one runs); a missing capture-all
            # leaves a no-match path carrying base through.
            base = apply(states, *calls(st.subject))
            legs: list[tuple[bool, bool]] = []
            for c in st.cases:
                legs += step_list(c.body, base)
            if not any(_irrefutable(c) for c in st.cases):
                legs += base
            return dedup(legs)
        if isinstance(st, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
            apply(states, *calls(st))
            return []
        return apply(states, *calls(st))

    def step_list(stmts: list[ast.stmt], states: list[tuple[bool, bool]]) -> list[tuple[bool, bool]]:
        cur = states
        for st in stmts:
            if found:
                return []
            cur = step(st, cur)
        return cur

    step_list(body, [(False, False)])
    return found


def _arg_expr(call: ast.Call, index: int, name: str) -> ast.expr | None:
    """The positional arg at `index`, or the keyword arg `name`, else None."""
    if len(call.args) > index:
        return call.args[index]
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _is_str_literal(expr: ast.expr | None) -> bool:
    return isinstance(expr, ast.Constant) and isinstance(expr.value, str)


def _local_def_names(tree: ast.AST) -> frozenset[str]:
    """Every function name defined anywhere in the module. A verb call in a file
    that defines that verb is the library's own primitive (tackbox_report owns
    per-name fingerprints, D002) or a local shadow (D004), not a consumer call
    site, so the msg/dedup_key contract (D007/D008) does not apply to it."""
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


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


def _is_test_file(filename: str) -> bool:
    """A test file - a test_*.py / *_test.py basename, conftest.py, or a tests/
    path segment. TBX010/TBX011 (the new notify-gate and reporter-arg rules) skip
    tests, parity with Go _test.go and Java src/test; the swallow and test-skip
    rules keep running there."""
    p = filename.replace("\\", "/")
    base = p.rsplit("/", 1)[-1]
    if base == "conftest.py" or base.startswith("test_") or base.endswith("_test.py"):
        return True
    return "/tests/" in p or p.startswith("tests/")


class _Visitor(ast.NodeVisitor):
    def __init__(
        self,
        markers: MarkerIndex,
        skip_markers: MarkerIndex,
        reporter_names: frozenset[str],
        unittest_skip: bool,
        local_defs: frozenset[str] = frozenset(),
        in_test_file: bool = False,
    ):
        self.markers = markers
        self.skip_markers = skip_markers
        # Built-in tier-1 names are always recognized; declared tier-2 names extend them.
        self.reporter_names = _BUILTIN_REPORTERS | reporter_names
        # Double-lane capture set: recognized reporters minus the terminal panic.
        self.lane_captures = (_LANE_CAPTURE_BUILTINS | reporter_names) - {"report_panic"}
        self.unittest_skip = unittest_skip
        self.local_defs = local_defs
        self.in_test_file = in_test_file
        self.findings: list[tuple[int, int, str, str]] = []
        self._func_depth = 0

    def _add(self, node: ast.AST, code: str, detail: str = "") -> None:
        self.findings.append((node.lineno, node.col_offset, code, detail))

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
        if not self.in_test_file:
            self._check_reporter_args(node)  # TBX011 skips tests (D-4)
        self.generic_visit(node)

    def _check_reporter_args(self, node: ast.Call) -> None:
        """TBX011: a user-lane verb's msg must be a static literal (D007) and its
        dedup_key a well-formed literal (D008). Recognized by name; a call in a
        file that defines that verb (the library primitive or a local shadow) is
        exempt - the contract governs consumer call sites, not the owner."""
        if not isinstance(node.func, ast.Name):
            return
        name = node.func.id
        if name in self.local_defs:
            return
        if name in _MSG_VERBS:
            msg = _arg_expr(node, 0, "msg")
            if msg is not None and not _is_str_literal(msg):
                self._add(node, "TBX011")
        if name in _DEDUP_VERBS:
            key = _arg_expr(node, 3, "dedup_key")
            if key is None:
                self._add(node, "TBX011", _DEDUP_MISSING)
            elif not _is_str_literal(key):
                self._add(node, "TBX011", _DEDUP_NOT_LITERAL)
            elif not _DEDUP_KEY_RE.match(key.value):
                self._add(node, "TBX011", _DEDUP_BAD_FORMAT)

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

        if not self.in_test_file:
            self._check_notify(handler)  # TBX010 skips tests (D-4)

        if self._swallows(try_node, handler):
            self._add(try_node, "TBX001")

    def _check_notify(self, handler: ast.ExceptHandler) -> None:
        """TBX010: a notify carrying the caught error must be narrowed. For a
        typed catch the gate is the except type - a notify in a broad `except
        Exception` is a finding (marker-suppressible, last resort). On a narrow
        catch, a notify paired with a capture on one path is the double-lane."""
        if not _notifies(handler):
            return
        if _is_broad_except(handler):
            if not self.markers.suppresses(handler.body[0].lineno):
                self._add(handler, "TBX010")
        elif _lane_conflict(handler.body, self.lane_captures, handler.name):
            self._add(handler, "TBX010", _DOUBLE_LANE_MSG)

    def _swallows(self, try_node: ast.Try, handler: ast.ExceptHandler) -> bool:
        if _is_shutdown_carveout(handler):
            return False
        if self.markers.suppresses(handler.body[0].lineno):
            return False
        return _silent_path(handler.body, self.reporter_names, handler.name)


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
            _local_def_names(self.tree),
            _is_test_file(self.filename),
        )
        visitor.visit(self.tree)
        for line, col, code, detail in visitor.findings:
            msg = detail or MESSAGES[code]
            text = f"{code} {CODE_TO_ID[code]}: {msg}"
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

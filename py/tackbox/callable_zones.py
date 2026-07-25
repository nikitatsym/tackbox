"""Reliable non-body callable-header source zones.

The ast-grep subprocess seam and Svelte script extraction live in scopes.py
(D015). This module adds only the callable-boundary policy used by duplication
filtering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tackbox import scopes


@dataclass(frozen=True, order=True)
class Point:
    line: int
    column: int


@dataclass(frozen=True)
class Zone:
    start: Point
    end: Point


_CALLABLE_KINDS = {
    "python": (
        "function_definition",
        "lambda",
    ),
    "go": (
        "function_declaration",
        "method_declaration",
        "func_literal",
        "function_type",
        "method_elem",
    ),
    "java": (
        "method_declaration",
        "constructor_declaration",
        "lambda_expression",
    ),
    "javascript": (
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "generator_function",
        "method_definition",
        "arrow_function",
    ),
    "typescript": (
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "generator_function",
        "method_definition",
        "arrow_function",
        "function_signature",
        "method_signature",
        "abstract_method_signature",
        "call_signature",
        "construct_signature",
        "function_type",
        "constructor_type",
    ),
    "tsx": (
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "generator_function",
        "method_definition",
        "arrow_function",
        "function_signature",
        "method_signature",
        "abstract_method_signature",
        "call_signature",
        "construct_signature",
        "function_type",
        "constructor_type",
    ),
}

_WRAPPER_KINDS = {
    "java": ("annotation", "marker_annotation"),
    "javascript": ("decorator",),
    "typescript": ("decorator",),
    "tsx": ("decorator",),
}


def _any(kinds: tuple[str, ...]) -> str:
    return "any:\n" + "".join(f"  - kind: {kind}\n" for kind in kinds)


def _ruleset(language: str) -> str:
    kinds = _CALLABLE_KINDS[language]
    body = (
        "rule:\n  all:\n"
        + "    - "
        + _any(kinds).replace("\n", "\n      ").rstrip()
        + "\n"
        + "    - has:\n        field: body\n        pattern: $BODY\n"
    )
    rules = [
        scopes._rule(
            "callable",
            language,
            "rule:\n  " + _any(kinds).replace("\n", "\n  ").rstrip(),
        ),
        scopes._rule("body", language, body),
        scopes._rule(
            "parameters",
            language,
            "rule:\n  all:\n"
            + "    - "
            + _any(kinds).replace("\n", "\n      ").rstrip()
            + "\n"
            + "    - has:\n        field: parameters\n        pattern: $PARAMS\n",
        ),
        scopes._rule("err", language, "rule:\n  kind: ERROR"),
    ]
    wrappers = _WRAPPER_KINDS.get(language)
    if wrappers:
        rules.append(
            scopes._rule(
                "wrapper",
                language,
                "rule:\n  " + _any(wrappers).replace("\n", "\n  ").rstrip(),
            )
        )
    if language == "java":
        rules.append(scopes._rule("modifiers", language, "rule:\n  kind: modifiers"))
    if language in {"javascript", "typescript", "tsx"}:
        rules.append(
            scopes._rule("export", language, "rule:\n  kind: export_statement")
        )
    return "\n---\n".join(rules)


def _span(match: dict) -> tuple[int, int]:
    raw = match["range"]["byteOffset"]
    return raw["start"], raw["end"]


def _point(data: bytes, byte: int) -> Point:
    prefix = data[:byte]
    last = prefix.rfind(b"\n")
    return Point(prefix.count(b"\n"), byte if last < 0 else byte - last - 1)


def _skip_space(data: bytes, byte: int, limit: int) -> int:
    while byte < limit and data[byte] in b" \t\r\n":
        byte += 1
    return byte


def _before_nonspace(data: bytes, byte: int, floor: int) -> int:
    byte -= 1
    while byte >= floor and data[byte] in b" \t\r\n":
        byte -= 1
    return byte


def _prefix_without_external_wrappers(
    data: bytes,
    start: int,
    parameter_start: int | None,
    wrapper_spans: list[tuple[int, int]],
    modifier_spans: list[tuple[int, int]],
) -> int | None:
    """Exclude a leading wrapper run; reject wrappers interleaved with syntax."""
    ceiling = parameter_start if parameter_start is not None else len(data)
    external = sorted(
        span for span in wrapper_spans if start <= span[0] and span[1] <= ceiling
    )
    if not external:
        return start

    # Java declaration annotations are exactly those in the top-level modifiers
    # node. Parameter/type annotations outside that node remain header syntax.
    if modifier_spans:
        top = next((span for span in modifier_spans if span[0] == start), None)
        if top is None:
            return start
        external = [
            span
            for span in external
            if top[0] <= span[0] and span[1] <= top[1]
        ]
        if not external:
            return start

    cursor = _skip_space(data, start, ceiling)
    consumed = False
    for wrapper_start, wrapper_end in external:
        if wrapper_start != cursor:
            # A declaration wrapper after a modifier cannot be excluded while
            # retaining the earlier modifier in one contiguous interval.
            return None
        consumed = True
        cursor = _skip_space(data, wrapper_end, ceiling)
    return cursor if consumed else start


def _separator_end(
    data: bytes, language: str, callable_start: int, body: dict
) -> int | None:
    body_start, body_end = _span(body)
    if body_start == body_end:
        return None
    if data[body_start: body_start + 1] == b"{":
        return body_start + 1

    last = _before_nonspace(data, body_start, callable_start)
    if language == "python":
        return (
            last + 1
            if last >= callable_start and data[last:last + 1] == b":"
            else None
        )
    operator = b"->" if language == "java" else b"=>"
    first = last - len(operator) + 1
    if first >= callable_start and data[first:last + 1] == operator:
        return last + 1
    return None


def _bodyless_end(data: bytes, node_end: int) -> int:
    """Include a same-line explicit terminator that sits outside the AST node."""
    cursor = node_end
    while cursor < len(data) and data[cursor] in b" \t\r":
        cursor += 1
    if cursor < len(data) and data[cursor: cursor + 1] == b";":
        return cursor + 1
    return node_end


def zones_for_content(content: str, language: str) -> list[Zone]:
    """Extract zero-based, half-open header zones from one code unit."""
    if language not in _CALLABLE_KINDS:
        return []
    matches = scopes._ast_scan(content, _ruleset(language))
    by_rule: dict[str, list[dict]] = {}
    for match in matches:
        by_rule.setdefault(match["ruleId"], []).append(match)
    if by_rule.get("err"):
        return []

    data = content.encode("utf-8")
    body_by_span = {
        _span(match): scopes._mv(match, "BODY")
        for match in by_rule.get("body", [])
    }
    params_by_span = {
        _span(match): scopes._mv(match, "PARAMS")
        for match in by_rule.get("parameters", [])
    }
    wrappers = [_span(match) for match in by_rule.get("wrapper", [])]
    modifiers = [_span(match) for match in by_rule.get("modifiers", [])]
    exports = [_span(match) for match in by_rule.get("export", [])]
    zones: list[Zone] = []
    seen: set[tuple[int, int]] = set()

    for match in by_rule.get("callable", []):
        node_span = _span(match)
        start, node_end = node_span
        for export_start, export_end in exports:
            if export_start <= start and node_end <= export_end:
                prefix = data[export_start:start].decode("utf-8", errors="replace")
                if "export" in prefix and re.fullmatch(
                    r"(?:export|default|declare|\s)+", prefix
                ):
                    start = export_start
                break
        body = body_by_span.get(node_span)
        parameters = params_by_span.get(node_span)
        parameter_start = _span(parameters)[0] if parameters is not None else None
        adjusted = _prefix_without_external_wrappers(
            data, start, parameter_start, wrappers, modifiers
        )
        if adjusted is None:
            continue
        end = (
            _separator_end(data, language, adjusted, body)
            if body is not None
            else _bodyless_end(data, node_end)
        )
        if end is None or adjusted >= end:
            continue
        key = (adjusted, end)
        if key in seen:
            continue
        seen.add(key)
        zones.append(Zone(_point(data, adjusted), _point(data, end)))
    zones.sort(key=lambda zone: (zone.start, zone.end))
    return zones


def zones_for_file(root: Path, rel_path: str) -> list[Zone]:
    """Extract zones for one physical source file, mapping Svelte scripts."""
    language = scopes.language_for(rel_path)
    if language is None or language == "markdown":
        return []
    content = (root / rel_path).read_text(encoding="utf-8", errors="replace")
    if language != "svelte":
        return zones_for_content(content, language)

    data = content.encode("utf-8")
    zones: list[Zone] = []
    for script in scopes.svelte_scripts(content):
        script_text = data[script.start_byte:script.end_byte].decode(
            "utf-8", errors="replace"
        )
        local_data = script_text.encode("utf-8")
        for zone in zones_for_content(script_text, script.language):
            start = _byte_for_point(local_data, zone.start) + script.start_byte
            end = _byte_for_point(local_data, zone.end) + script.start_byte
            zones.append(Zone(_point(data, start), _point(data, end)))
    zones.sort(key=lambda zone: (zone.start, zone.end))
    return zones


def _byte_for_point(data: bytes, point: Point) -> int:
    lines = data.splitlines(keepends=True)
    if point.line >= len(lines):
        return len(data)
    return sum(len(line) for line in lines[:point.line]) + point.column

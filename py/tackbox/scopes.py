"""Resolve suppression markers to named-scope addresses via ast-grep.

The outline engine (D015): ast-grep (`ast-grep`, pinned) parses each
marker-bearing file, we assemble the enclosing scope chain in Python by byte
range containment, and serialize it to the identity schema (D014). The engine
is behind this module's contract - `resolve_file` returns addresses, callers
never see ast-grep. Swapping the engine is a local change.

Identity schema (D014), in one place:
- scope of a marker = innermost scope whose byte span contains the marker start;
- chain = every containing scope, outermost first, joined by `.`;
- a named scope contributes its (possibly synthesized) name, an anonymous scope
  its content hash `<h...>`;
- Go receiver methods prefix the receiver type; JS/TS functions assigned in a
  const/let/var declarator lift the variable name; Java method segments carry a
  normalized parameter-type signature; same-name siblings take an `@k` ordinal;
- a file whose own-language parse has ERROR nodes refuses resolution.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# The canonical executable, never the `sg` alias (collides with linux setgroups).
_AST_GREP = "ast-grep"


class ScopesError(RuntimeError):
    """ast-grep could not be run (absent binary, crash) - a CLI infra error."""


@dataclass(frozen=True)
class ResolvedMarker:
    address: str  # serialized `path` (file scope) or `path#chain`
    marker: str  # exact marker text (`keyword: reason`), as cli._markers extracts
    line: int  # 1-based line of the marker occurrence in the file


@dataclass
class FileResult:
    markers: list[ResolvedMarker] = field(default_factory=list)
    # True: the file's own-language parse has ERROR nodes; markers/entries for it
    # are unverifiable and must be reported as unresolvable, never guessed.
    unresolvable: bool = False


# Internal per-unit result: chain (segments) + marker text + line, address-free
# (the caller owns the file path). A code unit is a whole file or one Svelte
# script block.
@dataclass
class _Sub:
    markers: list[tuple[list[str], str, int, int]] = field(default_factory=list)  # chain,text,line,byte
    unresolvable: bool = False


# -- language dispatch -----------------------------------------------------

_EXT_LANG = {
    ".py": "python",
    ".go": "go",
    ".java": "java",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".md": "markdown",
    ".svelte": "svelte",
}

# Non-JavaScript scripts a `.svelte` `lang` attribute selects.
_SVELTE_SCRIPT_LANG = {"ts": "typescript", "typescript": "typescript"}


def _ext(path: str) -> str:
    dot = path.rfind(".")
    return path[dot:] if dot >= 0 else ""


def language_for(rel_path: str) -> str | None:
    return _EXT_LANG.get(_ext(rel_path.replace("\\", "/")))


# -- ast-grep invocation (the engine seam) ---------------------------------


def _rule(rid: str, lang: str, body: str) -> str:
    return f"id: {rid}\nlanguage: {lang}\n{body}"


def _ast_scan(content: str, ruleset: str) -> list[dict]:
    """Run one ast-grep scan over `content` on stdin; return the JSON matches.

    Each rule in `ruleset` tags its matches with `ruleId`. Content is fed on
    stdin with the language fixed by the rules - ast-grep does not register the
    `.svelte` extension and silently matches nothing on such paths, so a path is
    never handed to it."""
    try:
        proc = subprocess.run(
            [_AST_GREP, "scan", "--stdin", "--inline-rules", ruleset, "--json=compact"],
            input=content.encode("utf-8"),
            capture_output=True,
        )
    except OSError as e:
        raise ScopesError(f"cannot run ast-grep: {e}") from e
    # 0 = matches, 1 = no matches; anything else is a real engine failure.
    if proc.returncode not in (0, 1):
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise ScopesError(f"ast-grep scan failed ({proc.returncode}): {err}")
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    return json.loads(out) if out else []


def _bspan(m: dict) -> tuple[int, int]:
    b = m["range"]["byteOffset"]
    return b["start"], b["end"]


def _mv(m: dict, key: str) -> dict:
    return m["metaVariables"]["single"][key]


# -- identity-schema serialization -----------------------------------------

_SEG_ESCAPE = {"\\": "\\\\", ".": "\\.", "#": "\\#", ":": "\\:", "@": "\\@"}
_PATH_ESCAPE = {"\\": "\\\\", "#": "\\#", ":": "\\:"}


def _escape(s: str, table: dict[str, str]) -> str:
    return "".join(table.get(ch, ch) for ch in s)


def escape_segment(name: str) -> str:
    return _escape(name, _SEG_ESCAPE)


def escape_path(rel_path: str) -> str:
    # Dots stay literal: `.` separates chain segments, not path characters.
    return _escape(rel_path, _PATH_ESCAPE)


def _address(rel_path: str, chain: list[str]) -> str:
    base = escape_path(rel_path.replace("\\", "/"))
    return f"{base}#{'.'.join(chain)}" if chain else base


def _anon_hash(node_text: str) -> str:
    """`<h...>`: sha256 over the anonymous node's text with each maximal run of
    whitespace collapsed to one space and the leading/trailing run stripped,
    lowercase hex truncated to 8."""
    normalized = " ".join(node_text.split())
    return f"<h{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:8]}>"


# -- scope-node model ------------------------------------------------------


@dataclass
class _Scope:
    start: int
    end: int
    base: str  # serialized segment WITHOUT the ordinal suffix


def _apply_ordinals(scopes: list[_Scope]) -> dict[int, str]:
    """Serialized segment per scope, with `@k` ordinals for same-name siblings.

    Siblings = scopes sharing the innermost containing scope (their parent) and
    an identical serialized base. The k-th (k>=2) in document order takes `@k`.
    """
    def parent_of(i: int) -> int:
        s = scopes[i]
        best, best_span = -1, None
        for j, o in enumerate(scopes):
            if j == i:
                continue
            if o.start <= s.start and s.end <= o.end and (o.start, o.end) != (s.start, s.end):
                span = o.end - o.start
                if best_span is None or span < best_span:
                    best, best_span = j, span
        return best

    parents = {i: parent_of(i) for i in range(len(scopes))}
    groups: dict[tuple[int, str], list[int]] = {}
    for i, s in enumerate(scopes):
        groups.setdefault((parents[i], s.base), []).append(i)
    serialized: dict[int, str] = {}
    for members in groups.values():
        members.sort(key=lambda i: (scopes[i].start, scopes[i].end))
        for k, i in enumerate(members, start=1):
            serialized[i] = scopes[i].base if k == 1 else f"{scopes[i].base}@{k}"
    return serialized


def _chain_for(cbyte: int, scopes: list[_Scope], serialized: dict[int, str]) -> list[str]:
    containing = [i for i, s in enumerate(scopes) if s.start <= cbyte < s.end]
    containing.sort(key=lambda i: (scopes[i].start, -scopes[i].end))  # outer -> inner
    return [serialized[i] for i in containing]


# -- marker extraction (mirrors cli._markers, adds line/byte offset) --------


def _markers_in(node_text: str, node_start_byte: int, node_start_line: int,
                marker_re: re.Pattern[str],
                only_ranges: list[tuple[int, int]] | None = None) -> list[tuple[str, int, int]]:
    """(marker text, marker start byte, 1-based line) for each marker occurrence
    in a comment node. Marker text = the match through end of its line, stripped
    - the exact `keyword: reason` text cli._markers yields, unchanged. When
    `only_ranges` is given, a marker counts only if its start falls inside one
    (used for markdown, where the marker must sit inside an HTML comment)."""
    out: list[tuple[str, int, int]] = []
    for m in marker_re.finditer(node_text):
        if only_ranges is not None and not any(s <= m.start() < e for s, e in only_ranges):
            continue
        eol = node_text.find("\n", m.start())
        text = node_text[m.start(): len(node_text) if eol < 0 else eol].strip()
        byte = node_start_byte + len(node_text[: m.start()].encode("utf-8"))
        line = node_start_line + node_text.count("\n", 0, m.start())
        out.append((text, byte, line))
    return out


# Markdown markers live inside HTML comments only (block-level html_block or an
# inline `<!-- -->` in a paragraph's opaque `inline` node); a marker keyword in
# plain prose is not a comment and must not be inventoried.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


# -- per-language rule sets (strictly separate; a kind unknown to a grammar
#    zeroes the whole rule) -------------------------------------------------

_DECL_KINDS = {
    "python": ["function_definition", "class_definition"],
    "go": ["function_declaration", "method_declaration", "method_elem", "type_spec"],
    "java": ["class_declaration", "interface_declaration", "enum_declaration",
             "record_declaration", "method_declaration", "constructor_declaration"],
    "typescript": ["function_declaration", "class_declaration", "method_definition",
                   "internal_module", "abstract_class_declaration"],
    "tsx": ["function_declaration", "class_declaration", "method_definition",
            "internal_module", "abstract_class_declaration"],
    "javascript": ["function_declaration", "class_declaration", "method_definition"],
}
_ANON_KINDS = {
    "python": ["lambda"],
    "go": ["func_literal"],
    "java": ["lambda_expression", "object_creation_expression", "static_initializer"],
    "typescript": ["arrow_function", "function_expression", "object", "generator_function"],
    "tsx": ["arrow_function", "function_expression", "object", "generator_function"],
    "javascript": ["arrow_function", "function_expression", "object", "generator_function"],
}
_COMMENT_KINDS = {
    "python": ["comment"],
    "go": ["comment"],
    "java": ["line_comment", "block_comment"],
    "typescript": ["comment"],
    "tsx": ["comment"],
    "javascript": ["comment"],
}
_LIFT_LANGS = frozenset({"typescript", "tsx", "javascript"})


def _any_kinds(kinds: list[str]) -> str:
    return "rule:\n  any:\n" + "".join(f"    - kind: {k}\n" for k in kinds)


def _code_ruleset(lang: str) -> str:
    rules = [
        _rule("decls", lang, _any_kinds(_DECL_KINDS[lang]) + "  has: {field: name, pattern: $NAME}"),
        _rule("anon", lang, _any_kinds(_ANON_KINDS[lang])),
        _rule("comment", lang, _any_kinds(_COMMENT_KINDS[lang])),
        _rule("err", lang, "rule:\n  kind: ERROR"),
    ]
    if lang in _LIFT_LANGS:
        rules.append(_rule("obj", lang, "rule:\n  kind: object"))
        rules.append(_rule("lift", lang,
            "rule:\n  kind: variable_declarator\n  all:\n"
            "    - has: {field: name, pattern: $NAME}\n"
            "    - has: {field: value, pattern: $VAL}"))
    if lang == "go":
        rules.append(_rule("method", lang,
            "rule:\n  kind: method_declaration\n  all:\n"
            "    - has: {field: name, pattern: $NAME}\n"
            "    - has: {field: receiver, pattern: $RECV}"))
    if lang == "java":
        rules.append(_rule("method", lang,
            "rule:\n  any:\n    - kind: method_declaration\n    - kind: constructor_declaration\n"
            "  all:\n    - has: {field: name, pattern: $NAME}\n"
            "    - has: {field: parameters, pattern: $PARAMS}"))
        rules.append(_rule("param", lang, "rule:\n  kind: formal_parameter\n  has: {field: type, pattern: $T}"))
        # spread_parameter has no `type` field; the varargs type is the text up to
        # and including `...` (the sole place `...` appears in a parameter).
        rules.append(_rule("vararg", lang, "rule:\n  kind: spread_parameter"))
    return "\n---\n".join(rules)


def _go_receiver_type(recv_text: str) -> str:
    tokens = recv_text.strip("()").split()
    return tokens[-1].lstrip("*") if tokens else "?"


def _java_signature(params_span: tuple[int, int], params: list[dict], varargs: list[dict]) -> str:
    types: list[tuple[int, str]] = []
    for p in params:
        sp = _bspan(p)
        if params_span[0] <= sp[0] and sp[1] <= params_span[1]:
            types.append((sp[0], "".join(_mv(p, "T")["text"].split())))
    for p in varargs:
        sp = _bspan(p)
        if params_span[0] <= sp[0] and sp[1] <= params_span[1]:
            text = p["text"]
            cut = text.find("...")
            head = text[: cut + 3] if cut >= 0 else text
            types.append((sp[0], "".join(head.split())))
    types.sort()
    return "(" + ",".join(t for _, t in types) + ")"


def _resolve_code(content: str, lang: str, marker_re: re.Pattern[str]) -> _Sub:
    """Resolve one code unit (a whole file, or one extracted Svelte script)."""
    matches = _ast_scan(content, _code_ruleset(lang))
    by_rule: dict[str, list[dict]] = {}
    for m in matches:
        by_rule.setdefault(m["ruleId"], []).append(m)
    if by_rule.get("err"):
        return _Sub(unresolvable=True)

    scopes: list[_Scope] = []
    span_to_scope: dict[tuple[int, int], _Scope] = {}
    for d in by_rule.get("decls", []):
        sp = _bspan(d)
        s = _Scope(sp[0], sp[1], escape_segment(_mv(d, "NAME")["text"]))
        scopes.append(s)
        span_to_scope[sp] = s

    obj_spans = {_bspan(o) for o in by_rule.get("obj", [])}
    anon_spans: dict[tuple[int, int], _Scope] = {}
    for a in by_rule.get("anon", []):
        sp = _bspan(a)
        if sp in span_to_scope:
            continue
        s = _Scope(sp[0], sp[1], _anon_hash(a["text"]))
        scopes.append(s)
        anon_spans[sp] = s

    # Go: rewrite the method segment as Receiver.name.
    if lang == "go":
        for meth in by_rule.get("method", []):
            sp = _bspan(meth)
            if sp in span_to_scope:
                recv = _go_receiver_type(_mv(meth, "RECV")["text"])
                span_to_scope[sp].base = escape_segment(recv) + "." + escape_segment(_mv(meth, "NAME")["text"])

    # Java: overlay the normalized parameter-type signature onto method segments.
    if lang == "java":
        params, varargs = by_rule.get("param", []), by_rule.get("vararg", [])
        for meth in by_rule.get("method", []):
            sp = _bspan(meth)
            if sp in span_to_scope:
                pspan = _mv(meth, "PARAMS")["range"]["byteOffset"]
                sig = _java_signature((pspan["start"], pspan["end"]), params, varargs)
                span_to_scope[sp].base = escape_segment(_mv(meth, "NAME")["text"] + sig)

    # JS/TS: const-lift an anonymous function (not an object) to the variable name.
    if lang in _LIFT_LANGS:
        for lift in by_rule.get("lift", []):
            val = _mv(lift, "VAL")["range"]["byteOffset"]
            vspan = (val["start"], val["end"])
            if vspan in anon_spans and vspan not in obj_spans:
                anon_spans[vspan].base = escape_segment(_mv(lift, "NAME")["text"])

    serialized = _apply_ordinals(scopes)
    sub = _Sub()
    for c in by_rule.get("comment", []):
        cstart = _bspan(c)[0]
        cline = c["range"]["start"]["line"] + 1
        chain = _chain_for(cstart, scopes, serialized)
        for text, byte, line in _markers_in(c["text"], cstart, cline, marker_re):
            sub.markers.append((chain, text, line, byte))
    return sub


# -- markdown (outline by a level stack, not containment) ------------------


def _md_level(text: str) -> int:
    t = text.strip("\n")
    stripped = t.strip()
    if stripped.startswith("#"):
        return len(re.match(r"#+", stripped).group(0))
    lines = t.splitlines()
    if len(lines) >= 2 and set(lines[-1].strip()) == {"="}:
        return 1
    if len(lines) >= 2 and set(lines[-1].strip()) == {"-"}:
        return 2
    return 99


def _md_title(text: str) -> str:
    t = text.strip("\n")
    if t.lstrip().startswith("#"):
        return t.strip().lstrip("#").strip()
    return t.splitlines()[0].strip()


def _resolve_markdown(content: str, marker_re: re.Pattern[str]) -> _Sub:
    # html_block = block-level `<!-- -->`; inline = a paragraph's opaque inline
    # content, which may embed an inline `<!-- -->`. Both are comment-gated below.
    ruleset = "\n---\n".join([
        _rule("headings", "markdown", "rule:\n  any:\n    - kind: atx_heading\n    - kind: setext_heading"),
        _rule("comment", "markdown", "rule:\n  any:\n    - kind: html_block\n    - kind: inline"),
    ])
    matches = _ast_scan(content, ruleset)
    headings, comments = [], []
    for m in matches:
        (headings if m["ruleId"] == "headings" else comments).append(m)
    headings.sort(key=lambda h: _bspan(h)[0])

    # Global pass: serialized title (with @k ordinal) per heading, keyed by the
    # outline stack it sits under.
    stack: list[tuple[int, str]] = []
    ser: list[tuple[int, int, str]] = []  # (start, level, serialized)
    counts: dict[tuple[tuple[str, ...], str], int] = {}
    for h in headings:
        level, title = _md_level(h["text"]), _md_title(h["text"])
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = tuple(s for _, s in stack)
        counts[(parent, title)] = counts.get((parent, title), 0) + 1
        k = counts[(parent, title)]
        s = escape_segment(title) if k == 1 else f"{escape_segment(title)}@{k}"
        stack.append((level, s))
        ser.append((_bspan(h)[0], level, s))

    def chain_at(pos: int) -> list[str]:
        st: list[tuple[int, str]] = []
        for start, level, s in ser:
            if start > pos:
                break
            while st and st[-1][0] >= level:
                st.pop()
            st.append((level, s))
        return [s for _, s in st]

    sub = _Sub()
    for c in comments:
        node_text = c["text"]
        cstart = _bspan(c)[0]
        cline = c["range"]["start"]["line"] + 1
        ranges = [(m.start(), m.end()) for m in _HTML_COMMENT_RE.finditer(node_text)]
        for text, byte, line in _markers_in(node_text, cstart, cline, marker_re, only_ranges=ranges):
            sub.markers.append((chain_at(byte), text, line, byte))  # outline chain at the marker
    return sub


# -- Svelte (html container parse + script extraction) ---------------------

# raw_text is scoped to script elements: tree-sitter-html also emits raw_text for
# <style> content, and feeding CSS to the JS/TS parser would refuse the file
# (ERROR nodes). <style> takes no marker - excluded from the inventory.
_SVELTE_HTML_RULES = "\n---\n".join([
    _rule("raw", "html", "rule:\n  kind: raw_text\n  inside: {kind: script_element}"),
    _rule("comment", "html", "rule:\n  kind: comment"),
])
_SVELTE_STYLE_RE = re.compile(rb"<style\b[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)


def _resolve_svelte(raw: bytes, marker_re: re.Pattern[str]) -> _Sub:
    content = raw.decode("utf-8", errors="replace")
    matches = _ast_scan(content, _SVELTE_HTML_RULES)
    scripts = [m for m in matches if m["ruleId"] == "raw"]
    html_comments = [m for m in matches if m["ruleId"] == "comment"]

    sub = _Sub()
    covered: list[tuple[int, int]] = []
    for s in scripts:
        b = s["range"]["byteOffset"]
        start, end = b["start"], b["end"]
        covered.append((start, end))
        tag = raw[raw.rfind(b"<script", 0, start): start].decode("utf-8", errors="replace")
        m = re.search(r'\blang\s*=\s*[\'"]?([A-Za-z0-9]+)', tag)
        script_lang = _SVELTE_SCRIPT_LANG.get(m.group(1).lower() if m else "", "javascript")
        script_sub = _resolve_code(raw[start:end].decode("utf-8"), script_lang, marker_re)
        if script_sub.unresolvable:
            return _Sub(unresolvable=True)  # a script that does not parse refuses the file
        for chain, text, _local_line, local_byte in script_sub.markers:
            line = raw.count(b"\n", 0, start + local_byte) + 1
            sub.markers.append((chain, text, line, start + local_byte))

    # Template HTML-comment markers (AST-precise) anchor at file scope.
    for c in html_comments:
        cstart = c["range"]["byteOffset"]["start"]
        cline = c["range"]["start"]["line"] + 1
        for text, byte, line in _markers_in(c["text"], cstart, cline, marker_re):
            sub.markers.append(([], text, line, byte))

    # In-expression `//` markers in the remaining (non-script, non-style,
    # non-html-comment) text, scanned textually (a superset, over-ask direction).
    # Blanking keeps newlines so byte line numbers stay accurate; script and
    # html-comment regions are blanked so their markers are not double-counted.
    blanked = bytearray(raw)
    for c in html_comments:
        b = c["range"]["byteOffset"]
        covered.append((b["start"], b["end"]))
    for mm in _SVELTE_STYLE_RE.finditer(raw):
        covered.append((mm.start(), mm.end()))
    for start, end in covered:
        for i in range(start, end):
            if blanked[i] != 0x0A:
                blanked[i] = 0x20
    scan = bytes(blanked)
    for hit in re.finditer(rb"//", scan):
        eol = scan.find(b"\n", hit.start())
        rest = scan[hit.start() + 2: len(scan) if eol < 0 else eol].decode("utf-8", errors="replace")
        mk = marker_re.search(rest)
        if mk is not None:
            sub.markers.append(([], rest[mk.start():].strip(), scan.count(b"\n", 0, hit.start()) + 1, hit.start()))
    return sub


# -- public resolution -----------------------------------------------------


def resolve_file(root: Path, rel: str, marker_re: re.Pattern[str]) -> FileResult:
    """Resolve `root/rel`'s markers to addresses, or flag it unresolvable.

    A file with no marker-like text (AST-precise markers are a subset of the
    textual match) resolves to no markers without invoking the engine."""
    lang = language_for(rel)
    if lang is None:
        return FileResult()
    path = root / rel
    if not path.is_file():
        return FileResult()
    raw = path.read_bytes()
    if b"\x00" in raw:
        return FileResult()  # binary: no marker text to resolve
    content = raw.decode("utf-8", errors="replace")
    if not marker_re.search(content):
        return FileResult()  # no comment marker can exist without a textual match

    if lang == "markdown":
        sub = _resolve_markdown(content, marker_re)
    elif lang == "svelte":
        sub = _resolve_svelte(raw, marker_re)
    else:
        sub = _resolve_code(content, lang, marker_re)

    if sub.unresolvable:
        return FileResult(unresolvable=True)
    out = FileResult()
    for chain, text, line, _byte in sub.markers:
        out.markers.append(ResolvedMarker(_address(rel, chain), text, line))
    return out


def has_marker_text(root: Path, rel: str, marker_re: re.Pattern[str]) -> bool:
    path = root / rel
    if not path.is_file():
        return False
    raw = path.read_bytes()
    if b"\x00" in raw:
        return False
    return bool(marker_re.search(raw.decode("utf-8", errors="replace")))

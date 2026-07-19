"""scopes.resolve_file: the identity schema (D014) over the ast-grep outline.

Corpus is generated inline and written to tmp files with the real extension -
never stored as tracked fixtures, so tackbox self-lint never scans them into
its own marker inventory. Ported from the ast-grep spike corpus; where the
spike's exploratory EXPECT comments differ from the plan's fixed contract
(anonymous scopes contribute hash segments, JS/TS const-lift, always-present
Java signatures), the plan wins and these assertions encode it.
"""

from __future__ import annotations

import hashlib

import pytest
from tackbox import scopes
from tackbox.cli import _MARKER_RE


def resolve(root, name, content):
    (root / name).write_text(content, encoding="utf-8")
    return scopes.resolve_file(root, name, _MARKER_RE)


def addrs(result):
    """{1-based line: address} for each resolved marker."""
    return {m.line: m.address for m in result.markers}


def hexhash(body: str) -> str:
    return f"<h{hashlib.sha256(' '.join(body.split()).encode('utf-8')).hexdigest()[:8]}>"


# -- scope-position rule (trailing / in-body / above-declaration) ----------

def test_python_scope_positions(tmp_path):
    src = (
        "def foo():\n"
        "    # no-report: in-body addresses foo\n"
        "    return 1\n"
        "\n"
        "# no-report: above def bar addresses the parent (file)\n"
        "def bar():\n"
        "    return 2\n"
        "\n"
        "def baz():  # no-report: trailing on signature addresses baz\n"
        "    return 3\n"
    )
    a = addrs(resolve(tmp_path, "s.py", src))
    assert a[2] == "s.py#foo"
    assert a[5] == "s.py"
    assert a[9] == "s.py#baz"


def test_python_nesting_ordinal_lambda(tmp_path):
    src = (
        "x = 1  # no-report: module scope\n"
        "def outer():\n"
        "    def inner():\n"
        "        return 1  # no-report: nested def\n"
        "class Outer:\n"
        "    class Inner:\n"
        "        def m(self):\n"
        "            return 1  # no-report: class-in-class\n"
        "sq = lambda v: v * v  # no-report: trailing after lambda is file scope\n"
        "def dup():\n"
        "    return 1  # no-report: first dup\n"
        "def dup():\n"
        "    return 2  # no-report: second dup\n"
    )
    a = addrs(resolve(tmp_path, "s.py", src))
    assert a[1] == "s.py"
    assert a[4] == "s.py#outer.inner"
    assert a[8] == "s.py#Outer.Inner.m"
    assert a[9] == "s.py"
    assert a[11] == "s.py#dup"
    assert a[13] == "s.py#dup@2"  # same-name sibling ordinal


def test_string_literal_lookalike_excluded(tmp_path):
    src = (
        'LIT = "contains no-report: not-a-marker"  # real trailing? no, in string\n'
        'DOC = """\nno-report: also-not-a-marker inside a string\n"""\n'
        "y = 1  # no-report: the only real marker\n"
    )
    result = resolve(tmp_path, "s.py", src)
    # Exactly one AST-precise marker; the two string-literal lookalikes excluded.
    assert [m.line for m in result.markers] == [5]
    assert result.markers[0].address == "s.py"


# -- Go receiver synthesis + anonymous hash segments -----------------------

def test_go_receiver_and_anonymous(tmp_path):
    src = (
        "package main\n"
        "func Top() {\n"
        "\tx() // no-report: top func\n"
        "}\n"
        "type Server struct{ name string }\n"
        "func (s Server) Name() string {\n"
        "\treturn s.name // no-report: value receiver\n"
        "}\n"
        "func (s *Server) Set(n string) {\n"
        "\ts.name = n // no-report: pointer receiver\n"
        "}\n"
        "var h = func(x int) int {\n"
        "\treturn x // no-report: func literal in var is anon\n"
        "}\n"
        "type Greeter interface {\n"
        "\tGreet() string // no-report: interface method trailing -> parent\n"
        "}\n"
    )
    a = addrs(resolve(tmp_path, "s.go", src))
    assert a[3] == "s.go#Top"
    assert a[7] == "s.go#Server.Name"
    assert a[10] == "s.go#Server.Set"
    assert a[13].startswith("s.go#<h") and a[13].endswith(">")  # anon func literal -> hash
    assert a[16] == "s.go#Greeter"  # bodyless method's trailing comment -> interface


def test_anon_hash_contract():
    # The hash function directly: whitespace-collapsed, sha256, 8 hex.
    body = "func () {\n\t x()  \n}"
    assert scopes._anon_hash(body) == hexhash("func () { x() }")


def test_anon_hash_changes_with_body(tmp_path):
    one = resolve(tmp_path, "a.go", "package main\nvar h = func() { a() // no-report: x\n}\n")
    two = resolve(tmp_path, "b.go", "package main\nvar h = func() { bbb() // no-report: x\n}\n")
    h1 = one.markers[0].address
    h2 = two.markers[0].address
    assert h1 != h2 and h1.startswith("a.go#<h") and h2.startswith("b.go#<h")
    # deterministic: a second resolve reproduces the identical hash.
    assert resolve(tmp_path, "a.go", "package main\nvar h = func() { a() // no-report: x\n}\n").markers[0].address == h1


# -- Java overloads: signature normalization + always-present -------------

def test_java_signatures_and_normalization(tmp_path):
    # Markers inside each method body so byte-containment lands on the method.
    src = (
        "class S {\n"
        "  int add(int a, int b) { return a; /* no-report: L2 */ }\n"
        "  double add(double a, double b) { return a; /* no-report: L3 */ }\n"
        "  String add(String a, String b, String c) { return a; /* no-report: L4 */ }\n"
        "  <T> T id(T v) { return v; /* no-report: L5 */ }\n"
        "  void arr(int[] xs) { x(); /* no-report: L6 */ }\n"
        "  void varg(String... xs) { x(); /* no-report: L7 */ }\n"
        "  void gen(java.util.Map<String, Integer> m) { x(); /* no-report: L8 */ }\n"
        "  void none() { x(); /* no-report: L9 always has a signature */ }\n"
        "}\n"
    )
    a = addrs(resolve(tmp_path, "S.java", src))
    assert a[2] == "S.java#S.add(int,int)"
    assert a[3] == "S.java#S.add(double,double)"
    assert a[4] == "S.java#S.add(String,String,String)"
    assert a[5] == "S.java#S.id(T)"
    assert a[6] == "S.java#S.arr(int[])"
    assert a[7] == r"S.java#S.varg(String\.\.\.)"  # varargs dots escaped in the segment
    assert a[8] == r"S.java#S.gen(java\.util\.Map<String,Integer>)"  # generics kept, dots escaped
    assert a[9] == "S.java#S.none()"


def test_java_anonymous_and_local_class(tmp_path):
    src = (
        "class S {\n"
        "  static int c;\n"
        "  static { c = 1; /* no-report: static init is anon */ }\n"
        "  Runnable m() {\n"
        "    return new Runnable() {\n"
        "      public void run() { x(); /* no-report: anon class body */ }\n"
        "    };\n"
        "  }\n"
        "  void local() {\n"
        "    class L { void p() { x(); /* no-report: local class named */ } }\n"
        "    new L();\n"
        "  }\n"
        "}\n"
    )
    a = addrs(resolve(tmp_path, "S.java", src))
    assert "#S.<h" in a[3]  # static initializer -> hash under S
    assert a[6].startswith("S.java#S.m().<h") and a[6].endswith(".run()")  # anon class -> hash
    assert a[10] == "S.java#S.local().L.p()"  # local class is a named scope


# -- JS/TS const-lift + object/IIFE hash + private-name escaping ------------

def test_ts_const_lift_and_object(tmp_path):
    src = (
        "const arrow = (x: number): number => {\n"
        "  return x; // no-report: const-lifted arrow name\n"
        "};\n"
        "class Widget {\n"
        "  #secret(): number { return 1; } // no-report: private method\n"
        "}\n"
        "const obj = {\n"
        "  greet() { return 1; /* no-report: object-literal method */ },\n"
        "};\n"
        "(function iife() { x(); // no-report: IIFE is anon\n"
        "})();\n"
    )
    a = addrs(resolve(tmp_path, "s.ts", src))
    assert a[2] == "s.ts#arrow"
    assert a[5] == r"s.ts#Widget.\#secret"  # `#` escaped inside the segment
    assert a[8].startswith("s.ts#<h") and a[8].endswith(">.greet")  # object hash + named method
    assert a[10].startswith("s.ts#<h") and a[10].endswith(">")  # IIFE function_expression -> hash


def test_tsx_arrow_component_lift(tmp_path):
    src = (
        "const Card = (props) => {\n"
        "  // no-report: tsx arrow component const-lifts\n"
        "  return null;\n"
        "};\n"
    )
    a = addrs(resolve(tmp_path, "C.tsx", src))
    assert a[2] == "C.tsx#Card"


def test_js_export_default_anon_is_hash(tmp_path):
    src = "export default function () {\n  return 1; // no-report: anon default\n}\n"
    a = addrs(resolve(tmp_path, "s.js", src))
    assert a[2].startswith("s.js#<h") and a[2].endswith(">")


# -- Markdown outline: ATX + setext + fenced inertness + @ ordinal ---------

def test_markdown_outline_and_fenced_code(tmp_path):
    src = (
        "# Top\n\n"
        "<!-- no-report: under top -->\n\n"
        "## Sub\n\n"
        "<!-- no-report: under top.sub -->\n\n"
        "Setext H1\n=========\n\n"
        "<!-- no-report: under setext h1 -->\n\n"
        "Setext H2\n---------\n\n"
        "<!-- no-report: under setext h1.h2 -->\n\n"
        "### Deep\n\n"
        "```\n"
        "<!-- no-report: inside a fence is inert -->\n"
        "# not a heading\n"
        "```\n"
    )
    a = addrs(resolve(tmp_path, "d.md", src))
    assert a[3] == "d.md#Top"
    assert a[7] == "d.md#Top.Sub"
    assert a[12] == "d.md#Setext H1"
    assert a[17] == "d.md#Setext H1.Setext H2"
    # the fenced comment produced no marker at all (inert):
    assert not any(line >= 22 for line in a)


def test_markdown_inline_html_comment_marker(tmp_path):
    # A block-level AND an inline HTML-comment marker are both inventoried; the
    # inline one must not be silently dropped (silent-pass is ruled out).
    src = (
        "# H\n\n"
        "<!-- no-report: block-level marker -->\n\n"
        "some text <!-- no-report: inline marker --> more text\n"
    )
    ms = resolve(tmp_path, "d.md", src).markers
    assert {m.line: m.address for m in ms} == {3: "d.md#H", 5: "d.md#H"}
    inline = next(m for m in ms if m.line == 5)
    assert inline.marker.startswith("no-report: inline marker")


def test_markdown_prose_mention_is_not_a_marker(tmp_path):
    # A marker keyword in plain prose (not inside an HTML comment) must NOT be
    # inventoried - only comment nodes participate (AST-precise).
    src = "# H\n\nThis paragraph mentions no-report: in prose, not a comment.\n"
    assert resolve(tmp_path, "d.md", src).markers == []


def test_markdown_at_escape_adversarial(tmp_path):
    # A heading literally titled `A@2` must be distinct from the second sibling
    # `A` (which takes the @2 ordinal) - both ways.
    src = (
        "# A@2\n\n<!-- no-report: literal A-at-2 -->\n\n"
        "# A\n\n<!-- no-report: first A -->\n\n"
        "# A\n\n<!-- no-report: second A -->\n"
    )
    a = addrs(resolve(tmp_path, "d.md", src))
    assert a[3] == r"d.md#A\@2"    # literal title `A@2`, escaped @
    assert a[7] == "d.md#A"        # first sibling A
    assert a[11] == "d.md#A@2"     # second sibling A, ordinal @2
    assert a[3] != a[11]           # distinct addresses both ways


# -- Svelte: html-located scripts, module attrs, offset, template forms ----

def test_svelte_script_marker_and_offset(tmp_path):
    src = (
        '<script module lang="ts">\n'
        "  // no-report: module top-level -> file scope\n"
        "  export const V = 1\n"
        "</script>\n"
        '<script lang="ts">\n'
        "  function outer() {\n"
        "    const inner = (x) => {\n"
        "      // no-report: nested const-lifted arrow in instance script\n"
        "      return x;\n"
        "    };\n"
        "  }\n"
        "</script>\n"
        "\n"
        "{#each rows as r}\n"
        "  <li onclick={() => outer()}>{r}</li>\n"
        "{/each}\n"
    )
    a = addrs(resolve(tmp_path, "C.svelte", src))
    assert a[2] == "C.svelte"  # module-script top level anchors at file scope
    assert a[8] == "C.svelte#outer.inner"  # instance script chain, offset mapped to line 8


def test_svelte_template_marker_forms(tmp_path):
    # HTML-comment template marker and in-expression `//` marker both anchor at
    # file scope; the commented-out <script> is not a phantom block.
    html_comment = (
        "<script>\n  function f() {}\n</script>\n"
        "<!-- no-report: html comment template marker -->\n"
        "<button onclick={() => { try { f() } catch (e) {} }}>go</button>\n"
    )
    a = addrs(resolve(tmp_path, "h.svelte", html_comment))
    assert a[4] == "h.svelte"

    js_comment = (
        "<script>\n  function f() {}\n</script>\n"
        "<button onclick={() => {\n"
        "  // no-report: in-expression template marker\n"
        "  try { f() } catch (e) {}\n"
        "}}>go</button>\n"
    )
    a = addrs(resolve(tmp_path, "j.svelte", js_comment))
    assert a[5] == "j.svelte"

    commented = (
        '<script lang="ts">\n  let x = 0 // no-report: real script marker\n</script>\n'
        "<!-- <script>commented out</script> -->\n<p>{x}</p>\n"
    )
    r = resolve(tmp_path, "c.svelte", commented)
    # exactly one marker (the real script one); the commented <script> is inert.
    assert [m.line for m in r.markers] == [2]


def test_svelte_generics_trap_extracts(tmp_path):
    # A `generics="...>"` attribute must not break script extraction (regex does;
    # the html parse does not). The marker inside the script resolves.
    src = (
        '<script lang="ts" generics="T extends Record<string, number>">\n'
        "  let a = 0 // no-report: after a generics-with-gt attribute\n"
        "</script>\n<p>ok</p>\n"
    )
    a = addrs(resolve(tmp_path, "g.svelte", src))
    assert a[2] == "g.svelte"


def test_svelte_context_module_svelte4(tmp_path):
    # Svelte 4 `context="module"` and an instance block coexist; a chain inside
    # the module script resolves, the instance top-level anchors at file scope.
    src = (
        '<script context="module">\n'
        "  export function shared() {\n"
        "    helper(); // no-report: inside a svelte-4 context=module function\n"
        "  }\n"
        "</script>\n"
        "<script>\n"
        "  let y = 0; // no-report: instance-script top level\n"
        "</script>\n"
        "<p>{y}</p>\n"
    )
    a = addrs(resolve(tmp_path, "M.svelte", src))
    assert a[3] == "M.svelte#shared"
    assert a[7] == "M.svelte"


def test_svelte_style_block_does_not_refuse_file(tmp_path):
    # <style> content is dispatched to no rule: its raw_text must never reach the
    # JS parser, or a real-CSS style block (invalid JS) would refuse the whole
    # file and lose the script marker.
    src = (
        "<script>\n  let x = 1 // no-report: real script marker\n</script>\n"
        '<p class="a">{x}</p>\n'
        "<style>\n  p:hover { color: red; }\n  .a > span { margin: 0 auto; }\n</style>\n"
    )
    r = resolve(tmp_path, "s.svelte", src)
    assert r.unresolvable is False
    assert [(m.line, m.address) for m in r.markers] == [(2, "s.svelte")]


def test_svelte_style_comment_excluded(tmp_path):
    # A CSS block comment inside <style> takes no marker (excluded region).
    src = (
        "<script>\n  let y = 0\n</script>\n"
        "<style>\n  /* no-report: css comment, not a marker */\n  p { color: blue; }\n</style>\n"
    )
    r = resolve(tmp_path, "s.svelte", src)
    assert r.unresolvable is False
    assert r.markers == []


def test_svelte_template_error_is_exempt(tmp_path):
    # A real component's template yields html ERROR nodes (mustaches) but its
    # script parses: the file is resolvable, not refused.
    src = (
        '<script lang="ts">\n  let z = 0 // no-report: valid script\n</script>\n'
        "{#each xs as x}<li>{x}</li>{/each}\n"
    )
    r = resolve(tmp_path, "t.svelte", src)
    assert r.unresolvable is False
    assert [m.line for m in r.markers] == [2]


def test_svelte_script_parse_error_refuses(tmp_path):
    # A script block that does not parse refuses the whole file (only the script
    # parse counts, not the template ERROR nodes).
    src = '<script lang="ts">\n  let z = ( // no-report: broken script\n</script>\n<p>hi</p>\n'
    r = resolve(tmp_path, "b.svelte", src)
    assert r.unresolvable is True
    assert r.markers == []


# -- ERROR-file refusal ----------------------------------------------------

def test_error_file_refused(tmp_path):
    # A genuine syntax error yields grammar ERROR nodes -> refuse, never guess.
    src = (
        "class C {\n"
        "  void a() {\n"
        "    if (true) {\n"
        "      x(); // no-report: inside the damaged region\n"
        "    // MISSING closing brace\n"
        "  }\n"
        "  void b() { y(); }\n"
        "}\n"
    )
    r = resolve(tmp_path, "C.java", src)
    assert r.unresolvable is True
    assert r.markers == []


def test_deterministic_resolution(tmp_path):
    src = "def f():\n    x() # no-report: a\n    y() # no-report: b\n"
    first = [(m.address, m.marker, m.line) for m in resolve(tmp_path, "s.py", src).markers]
    second = [(m.address, m.marker, m.line) for m in resolve(tmp_path, "s.py", src).markers]
    assert first == second


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

from __future__ import annotations

from pathlib import Path

import pytest
from tackbox import callable_zones


FIXTURES = Path(__file__).parent / "fixtures" / "callable_zones"


def _content(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _offset(content: str, point: callable_zones.Point) -> int:
    lines = content.splitlines(keepends=True)
    return sum(len(line.encode()) for line in lines[:point.line]) + point.column


def _texts(content: str, zones: list[callable_zones.Zone]) -> list[str]:
    data = content.encode()
    return [
        data[_offset(content, zone.start):_offset(content, zone.end)].decode()
        for zone in zones
    ]


@pytest.mark.parametrize(
    ("fixture", "language", "required", "count"),
    [
        (
            "python.py.txt",
            "python",
            [
                "async def decorated(",
                "def plain(",
                "def method(",
                "lambda value=default_call():",
            ],
            4,
        ),
        (
            "go.go.txt",
            "go",
            [
                "func Generic[T any]",
                "func (service *Service) Method",
                "func(value int) string {",
                "func(value int) (string, error)",
                "Method(value int) (string, error)",
            ],
            5,
        ),
        (
            "java.java.txt",
            "java",
            [
                "public <T> T annotated(@Internal T value) throws Checked {",
                "Fixture(String value) throws Checked {",
                "abstract String bodyless(String value) throws Checked;",
                "(String value) -> {",
                "(String value) ->",
                "String call(String value) throws Checked;",
            ],
            6,
        ),
        (
            "javascript.js.txt",
            "javascript",
            [
                "export async function named(",
                "function* (value = defaultCall()) {",
                "constructor(value) {",
                "get value() {",
                "set value(next) {",
                "[computedName()](value = defaultCall()) {",
                "method(value) {",
                "*generator(value) {",
                "({[computedKey()]: value}) => {",
                "(value = defaultCall()) =>",
            ],
            10,
        ),
        (
            "javascript.jsx.txt",
            "javascript",
            [
                "function Component({items}) {",
                "(event) =>",
                "function (item) {",
            ],
            3,
        ),
        (
            "typescript.ts.txt",
            "typescript",
            [
                "function overloaded(value: string): Result;",
                "function overloaded(value: number): Result;",
                "function overloaded(value: string | number): Result {",
                "method<T>(value: T): Result;",
                "(value: string): Result;",
                "new (value: string): Service;",
                "abstract [computedName()]<T>(",
                "constructor(value: string) {",
                "[computedName()]<T>(value: T = defaultCall<T>()): Result {",
                "(value: string = defaultCall()): Result => {",
                "(value: string): Result =>",
                "(value: string) => Result",
                "new (value: string) => Service",
            ],
            13,
        ),
        (
            "typescript.tsx.txt",
            "tsx",
            [
                "({items}: Props): JSX.Element =>",
                "(event: Event): void =>",
                "(item: string): JSX.Element =>",
            ],
            3,
        ),
    ],
)
def test_advertised_callable_forms(
    fixture: str, language: str, required: list[str], count: int
) -> None:
    content = _content(fixture)
    texts = _texts(content, callable_zones.zones_for_content(content, language))
    assert len(texts) == count, texts
    for header in required:
        assert any(header in text for text in texts), (header, texts)


def test_headers_stop_before_body_content_and_include_required_boundary() -> None:
    cases = [
        ("def f(x: int = call()) -> result():\n    body()\n", "python", ":", "body"),
        ("package p\nfunc f(x int) string { body(); return \"\" }\n", "go", "{", "body"),
        ("class C { String f(String x) throws E { body(); } }\n", "java", "{", "body"),
        ("const f = (x = call()) => body(x);\n", "javascript", "=>", "body"),
        ("class C { abstract f(x: T): R; }\n", "typescript", ";", ""),
    ]
    for content, language, boundary, body in cases:
        texts = _texts(content, callable_zones.zones_for_content(content, language))
        assert len(texts) == 1, texts
        assert texts[0].rstrip().endswith(boundary), texts[0]
        if body:
            assert body not in texts[0]


def test_empty_block_close_is_not_in_header() -> None:
    content = "const empty = () => {};\n"
    [text] = _texts(
        content, callable_zones.zones_for_content(content, "javascript")
    )
    assert text.endswith("{")
    assert not text.endswith("{}")


def test_python_external_decorator_is_excluded_but_annotations_are_included() -> None:
    content = _content("python.py.txt")
    texts = _texts(content, callable_zones.zones_for_content(content, "python"))
    decorated = next(text for text in texts if "decorated" in text)
    assert "@external" not in decorated
    assert "annotation_call()" in decorated
    assert "default_call()" in decorated
    assert "return_annotation()" in decorated


def test_java_external_annotation_is_excluded_but_parameter_annotation_is_included() -> None:
    content = _content("java.java.txt")
    texts = _texts(content, callable_zones.zones_for_content(content, "java"))
    annotated = next(text for text in texts if "annotated" in text)
    assert "@External" not in annotated
    assert annotated.startswith("public ")
    assert "@Internal" in annotated


@pytest.mark.parametrize(
    ("fixture", "language"),
    [
        ("javascript.js.txt", "javascript"),
        ("typescript.ts.txt", "typescript"),
    ],
)
def test_js_family_external_decorator_is_excluded(
    fixture: str, language: str
) -> None:
    content = _content(fixture)
    texts = _texts(content, callable_zones.zones_for_content(content, language))
    computed = next(
        text
        for text in texts
        if "[computedName()]<" in text or "[computedName()](" in text
    )
    assert "@external" not in computed


def test_interleaved_java_declaration_annotation_has_no_guessed_zone() -> None:
    content = "class C { public @External static String f() { return body(); } }\n"
    assert callable_zones.zones_for_content(content, "java") == []


def test_svelte_maps_script_zones_to_physical_points_only(tmp_path: Path) -> None:
    content = _content("component.svelte.txt")
    path = tmp_path / "Component.svelte"
    path.write_text(content, encoding="utf-8")
    texts = _texts(content, callable_zones.zones_for_file(tmp_path, path.name))
    assert len(texts) == 3, texts
    assert any("function instance" in text for text in texts)
    assert any("(value) =>" in text for text in texts)
    assert any("(value: Input = defaultCall()): Result => {" in text for text in texts)
    assert all("templateBody" not in text and ".ignored" not in text for text in texts)


def test_parse_error_returns_no_zone() -> None:
    assert callable_zones.zones_for_content("def broken(:\n", "python") == []


def test_ast_grep_failure_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_content: str, _ruleset: str) -> list[dict]:
        raise RuntimeError("ast-grep failed")

    monkeypatch.setattr(callable_zones.scopes, "_ast_scan", fail)
    with pytest.raises(RuntimeError, match="ast-grep failed"):
        callable_zones.zones_for_content("def f():\n    pass\n", "python")

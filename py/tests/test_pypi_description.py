import re
import runpy
import sys
import types
from html.parser import HTMLParser
from pathlib import Path

from markdown_it import MarkdownIt

REPO = Path(__file__).resolve().parents[2]
BLOB = "https://github.com/nikitatsym/tackbox/blob/main/"
RAW = "https://raw.githubusercontent.com/nikitatsym/tackbox/main/"


class _Destinations(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.found: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.found += [value for name, value in attrs if name in ("href", "src") and value]


def _rendered_destinations(markdown: str) -> list[str]:
    parser = _Destinations()
    parser.feed(MarkdownIt("commonmark").render(markdown))
    return parser.found


def _relative(destinations: list[str]) -> list[str]:
    # PyPI resolves in-page fragments itself, but no repo path.
    return [d for d in destinations if not re.match(r"[A-Za-z][A-Za-z0-9+.-]*:|#", d)]


def _setup_globals(monkeypatch) -> dict:
    # setuptools is a build-only dependency; a stub setup() captures the call.
    captured: dict = {}
    stub = types.ModuleType("setuptools")
    stub.setup = lambda **kwargs: captured.update(kwargs)
    monkeypatch.setitem(sys.modules, "setuptools", stub)
    namespace = runpy.run_path(str(REPO / "py" / "setup.py"))
    namespace["long_description"] = captured["long_description"]
    return namespace


def test_pypi_description_links_are_absolute_and_exist(monkeypatch):
    destinations = _rendered_destinations(_setup_globals(monkeypatch)["long_description"])
    assert _relative(destinations) == []
    repo_paths = [d.removeprefix(BLOB).removeprefix(RAW).split("#")[0]
                  for d in destinations if d.startswith((BLOB, RAW))]
    assert repo_paths
    assert [p for p in repo_paths if not (REPO / p).exists()] == []


def test_rewrite_keeps_code_and_urls_and_leaves_titled_links_for_the_check(monkeypatch):
    namespace = _setup_globals(monkeypatch)

    def rewrite(markdown: str) -> str:
        return namespace["_REPO_PATH"].sub(namespace["_github_url"], markdown)

    source = (
        "![logo](assets/a.png) [doc](docs/x.md#part) [top](#top) [web](https://e.org)\n\n"
        "`[span](docs/x.md)`\n\n"
        "```text\n[fenced](docs/x.md)\n```\n"
    )
    rewritten = rewrite(source)
    assert _rendered_destinations(rewritten) == [
        f"{RAW}assets/a.png", f"{BLOB}docs/x.md#part", "#top", "https://e.org",
    ]
    assert "`[span](docs/x.md)`" in rewritten
    assert "[fenced](docs/x.md)" in rewritten
    assert _relative(_rendered_destinations(rewrite('[notes](docs/x.md "Title")\n'))) == ["docs/x.md"]

"""PyPI long description: py/setup.py turns README repo paths into GitHub URLs."""

import re
import runpy
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BLOB = "https://github.com/nikitatsym/tackbox/blob/main/"
RAW = "https://raw.githubusercontent.com/nikitatsym/tackbox/main/"
# Inline links and images, reference definitions, and HTML attributes.
TARGET = re.compile(r"\]\(([^)\s]+)\)|^\[[^\]\n]+\]:\s*(\S+)|\b(?:href|src)=\"([^\"]+)\"", re.M)


def _setup_globals(monkeypatch) -> dict:
    # setuptools is a build-only dependency; a stub setup() captures the call.
    captured: dict = {}
    stub = types.ModuleType("setuptools")
    stub.setup = lambda **kwargs: captured.update(kwargs)
    monkeypatch.setitem(sys.modules, "setuptools", stub)
    namespace = runpy.run_path(str(REPO / "py" / "setup.py"))
    namespace["long_description"] = captured["long_description"]
    return namespace


def test_readme_links_on_pypi_are_absolute_and_exist(monkeypatch):
    text = _setup_globals(monkeypatch)["long_description"]
    targets = [next(g for g in m.groups() if g) for m in TARGET.finditer(text)]
    assert targets
    # PyPI resolves in-page fragments itself, but no relative path.
    assert [t for t in targets if not re.match(r"[A-Za-z][A-Za-z0-9+.-]*:|#", t)] == []
    repo_paths = [t.removeprefix(BLOB).removeprefix(RAW).split("#")[0]
                  for t in targets if t.startswith((BLOB, RAW))]
    assert repo_paths
    assert [p for p in repo_paths if not (REPO / p).exists()] == []


def test_rewrite_keeps_code_fragments_and_urls(monkeypatch):
    namespace = _setup_globals(monkeypatch)
    source = (
        "![logo](assets/a.png) [doc](docs/x.md#part) [top](#top) [web](https://e.org)\n"
        "`[span](docs/x.md)`\n"
        "```text\n[fenced](docs/x.md)\n```\n"
    )
    assert namespace["_REPO_PATH"].sub(namespace["_github_url"], source) == (
        f"![logo]({RAW}assets/a.png) [doc]({BLOB}docs/x.md#part) [top](#top) [web](https://e.org)\n"
        "`[span](docs/x.md)`\n"
        "```text\n[fenced](docs/x.md)\n```\n"
    )

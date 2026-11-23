import os
import re
from pathlib import Path

from setuptools import setup

# An inline link or image to a repo path: no URI scheme, not absolute, not an
# in-page fragment. Code spans and fences match first and stay literal.
_REPO_PATH = re.compile(
    r"(`+).*?\1|(!?)(\[[^\]\n]*\])\((?![A-Za-z][A-Za-z0-9+.-]*:|[/#])([^)\s]+)\)",
    re.DOTALL,
)


def _github_url(match: re.Match) -> str:
    if match[1]:
        return match[0]
    if match[2]:
        base = "https://raw.githubusercontent.com/nikitatsym/tackbox/main/"
    else:
        base = "https://github.com/nikitatsym/tackbox/blob/main/"
    return f"{match[2]}{match[3]}({base}{match[4]})"


# The PyPI long description is the repo-root README. Wheels always build
# in-tree (scripts/build_wheels.py and dev `uv run --directory py`), so the
# file is present; a missing README fails the build loudly. PyPI resolves no
# relative URLs, so repo paths become GitHub URLs on main.
def _long_description() -> str:
    readme = Path(__file__).resolve().parent.parent / "README.md"
    return _REPO_PATH.sub(_github_url, readme.read_text(encoding="utf-8"))


# flake8 hosts pyrules; analyzer bumps are reviewed rule changes.
# Android bundles the pinned grammar because ast-grep-cli has no Android wheel.
# Payloads use a machine store so thin releases do not duplicate them in uv.
setup(
    version=os.environ.get("TACKBOX_VERSION", "0.0.0"),
    install_requires=[
        "flake8==7.3.0",
        "pyflakes==3.4.0",
        'ast-grep-cli==0.45.1; sys_platform != "android"',
    ],
    long_description=_long_description(),
    long_description_content_type="text/markdown",
)

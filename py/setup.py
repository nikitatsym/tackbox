import os
from pathlib import Path

from setuptools import setup


# The PyPI long description is the repo-root README. Wheels always build
# in-tree (scripts/build_wheels.py and dev `uv run --directory py`), so the
# file is present; a missing README fails the build loudly. PyPI resolves no
# relative URLs, so the logo path is pinned to raw.githubusercontent.
def _long_description() -> str:
    readme = Path(__file__).resolve().parent.parent / "README.md"
    return readme.read_text(encoding="utf-8").replace(
        "](assets/",
        "](https://raw.githubusercontent.com/nikitatsym/tackbox/main/assets/",
    )


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

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
# ast-grep-cli is the outline engine (D015): a thin pip dependency (flake8
# precedent), pinned so a grammar bump that shifts resolved scope chains is a
# visible, reviewed version change (residual A7). ast-grep-cli publishes no
# Android wheel, so the Android thin wheel bundles the same pinned ast-grep
# (engines/manifest.json `ast_grep`) instead. The engine payloads
# (tackbox-engines, tackbox-opengrep) are NOT pip dependencies: they come from
# the machine store (see tackbox.engines.ensure_payload), fetched once per
# payload version so a thin patch bump never re-materializes them under uv.
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

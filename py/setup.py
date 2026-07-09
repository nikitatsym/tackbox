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


# flake8 hosts the py-native exception rules (the pyrules plugin); it ships in
# the thin wheel so `uvx tackbox` resolves it into the same env as the plugin.
# The engine binaries are NOT a pip dependency: they come from the machine
# store (see tackbox.engines.ensure_engines), fetched once per engines version
# so a thin patch bump never re-materializes ~350 MB of fat under uv.
setup(
    version=os.environ.get("TACKBOX_VERSION", "0.0.0"),
    install_requires=["flake8>=6"],
    long_description=_long_description(),
    long_description_content_type="text/markdown",
)

import os

from setuptools import setup


# flake8 hosts the py-native exception rules (the pyrules plugin); it ships in
# the thin wheel so `uvx tackbox` resolves it into the same env as the plugin.
# The engine binaries are NOT a pip dependency: they come from the machine
# store (see tackbox.engines.ensure_engines), fetched once per engines version
# so a thin patch bump never re-materializes ~350 MB of fat under uv.
setup(
    version=os.environ.get("TACKBOX_VERSION", "0.0.0"),
    install_requires=["flake8>=6"],
)

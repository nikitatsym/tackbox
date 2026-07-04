import os

from setuptools import setup


engines_version = os.environ.get("TACKBOX_ENGINES_VERSION")
# flake8 hosts the py-native exception rules (the pyrules plugin); it ships in
# the thin wheel so `uvx tackbox` resolves it into the same env as the plugin.
install_requires = ["flake8>=6"]
if engines_version:
    install_requires.append(f"tackbox-engines=={engines_version}")


setup(
    version=os.environ.get("TACKBOX_VERSION", "0.0.0"),
    install_requires=install_requires,
)

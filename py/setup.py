import os

from setuptools import setup


engines_version = os.environ.get("TACKBOX_ENGINES_VERSION")
install_requires = [f"tackbox-engines=={engines_version}"] if engines_version else []


setup(
    version=os.environ.get("TACKBOX_VERSION", "0.0.0"),
    install_requires=install_requires,
)

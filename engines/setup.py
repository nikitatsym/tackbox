import os

from setuptools import setup


setup(version=os.environ.get("TACKBOX_ENGINES_VERSION", "0.0.0"))

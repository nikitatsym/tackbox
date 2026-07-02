from __future__ import annotations

from pathlib import Path

__version__ = "0.0.0"


def root() -> Path:
    return Path(__file__).parent


def bin_dir() -> Path:
    return root() / "bin"


def vendor_dir() -> Path:
    return root() / "vendor"


def node_modules_dir() -> Path:
    return vendor_dir() / "node_modules"

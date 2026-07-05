"""Content digests shared across the cache, the engine store, the wheel
builder, and doctor.

One implementation so a build-time pin (engines.json) and a runtime verify
(the store, doctor) can never drift. This module is a leaf: it imports
nothing from the rest of the package, so cache.py and engines.py can both
depend on it without a cycle.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(root: Path) -> str:
    """Deterministic digest of a directory tree: sorted rel paths + content.

    File contents and relative paths only - never modes - so a payload that
    survives a wheel round-trip (which may reset the exec bit) still verifies.
    """
    h = hashlib.sha256()
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        h.update(f.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(sha256_file(f).encode())
        h.update(b"\0")
    return h.hexdigest()

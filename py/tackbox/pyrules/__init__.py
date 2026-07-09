"""tackbox python exception rules as a flake8 plugin (codes TBX001..TBX008).

The engine is invoked only in closed form by the tackbox CLI:
`flake8 --isolated --disable-noqa --select=TBX [--reporters=...] <files>`.
CODE_TO_ID is re-exported so the CLI's machine parser can map a TBX code back
to its canonical rule id without importing the checker internals.
"""

from __future__ import annotations

from .checker import Plugin
from .codes import CODE_TO_ID

__all__ = ["Plugin", "CODE_TO_ID"]

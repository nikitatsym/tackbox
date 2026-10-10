"""Tackbox's flake8 plugin and stable Python diagnostic identities.

The CLI selects TBX,F401,F841 in isolated, noqa-disabled flake8 runs.
The runner adapts pyflakes' side-effect import convention; the TBX plugin
retains its per-site failure-lane suppression metadata in machine output.
"""

from __future__ import annotations

from .checker import Plugin
from .codes import CODE_TO_ID

__all__ = ["Plugin", "CODE_TO_ID"]

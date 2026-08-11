"""Child-process seam: the one place tackbox spawns a tool and reads its output.

Every tool tackbox drives - git, go, node, java, opengrep, ast-grep, jscpd -
emits UTF-8 on every platform, so the interpreter's default encoding is never
the right reader. On Windows that default is the ANSI code page (cp1252), where
a byte such as 0x9d (the tail of a UTF-8 curly quote) raises inside the
`subprocess` reader thread - a place no caller can guard. `subprocess` does not
propagate that: it hands back a None stream, so the real failure resurfaces far
away as a type error on None. Both halves are closed here, once: UTF-8 decoding
for every child, and a loud check that a requested stream actually arrived.
"""

from __future__ import annotations

import subprocess

ENCODING = "utf-8"
# Tool output is data to parse or text to print, never a trust boundary: a
# replacement char degrades into a parse error the callers already handle, while
# a decode exception raised in a reader thread is unrecoverable anywhere.
ERRORS = "replace"


class ChildStreamError(RuntimeError):
    """A captured child stream never arrived - its reader thread died."""


def run(argv, **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run` with the child's streams decoded as UTF-8 text."""
    completed = subprocess.run(
        argv, text=True, encoding=ENCODING, errors=ERRORS, **kwargs
    )
    return _verified(argv, kwargs, completed)


def run_bytes(argv, **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run` with undecoded streams, for NUL-separated or binary output."""
    return _verified(argv, kwargs, subprocess.run(argv, **kwargs))


def decode(data: bytes) -> str:
    """Decode one already-captured stream exactly as `run` would."""
    return data.decode(ENCODING, errors=ERRORS)


def _verified(argv, kwargs: dict, completed):
    for name in ("stdout", "stderr"):
        if _is_captured(kwargs, name) and getattr(completed, name) is None:
            raise ChildStreamError(
                f"{name} of `{_argv_text(argv)}` was captured but never arrived: "
                "its reader thread died (its traceback precedes this one)"
            )
    return completed


def _is_captured(kwargs: dict, name: str) -> bool:
    return bool(kwargs.get("capture_output")) or kwargs.get(name) is subprocess.PIPE


def _argv_text(argv) -> str:
    return " ".join(str(a) for a in argv)

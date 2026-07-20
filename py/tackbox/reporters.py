"""Parse and validate `.tackbox/reporters` declarations.

A declaration names a repo-local function as a report sink: a call whose
callee resolves to it counts as a capture (subject to argument-flow), so
the swallow rules do not fire on a catch that hands the error to it. The
file lives at repo root; one declaration per line:

    <repo-relative-file>#<function>: <reason>
    <repo-relative-file>#<function> [usage]: <reason>

`[usage]` declares the opposite kind of sink: a deliberate diagnostic
exit (a CLI `usage()` helper) - never a capture; ERC003 frees its calls
outside err-branches and bans them inside. Untagged declarations are
capture sinks.

Empty lines are ignored; every other line must parse. The CLI checks that
each `<file>` exists and is inside the repo (here); symbol existence is the
resolving engine's job (eslint scope, erclint types).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

FILENAME = ".tackbox/reporters"

KIND_CAPTURE = "capture"
KIND_USAGE = "usage"

_KIND_RE = re.compile(r"^(?P<fn>.*\S)\s+\[(?P<kind>[a-z]+)\]$")

_JS_EXTS = frozenset([".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".svelte"])
_GO_EXTS = frozenset([".go"])
# python -> pyrules, java -> javalint; symbol existence is the engine's job.
# .java stays a known decl extension (javalint resolves the tier-2 symbol); the
# opengrep->javalint cutover changed the owner, not whether java sinks are valid.
_ENGINE_DECL_EXTS = frozenset([".py", ".java"])
_KNOWN_EXTS = _JS_EXTS | _GO_EXTS | _ENGINE_DECL_EXTS


class ReportersError(Exception):
    """A `.tackbox/reporters` parse or path-validation failure (CLI exit 2)."""


@dataclass(frozen=True)
class Declaration:
    file: str  # repo-relative, as written
    function: str
    reason: str
    kind: str = KIND_CAPTURE


def parse(text: str) -> list[Declaration]:
    decls: list[Declaration] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        hash_i = line.find("#")
        colon_i = line.find(":", hash_i + 1) if hash_i >= 0 else -1
        if hash_i <= 0 or colon_i < 0:
            raise ReportersError(
                f"{FILENAME}:{lineno}: expected '<file>#<function>: <reason>'"
            )
        file = line[:hash_i].strip()
        function = line[hash_i + 1 : colon_i].strip()
        reason = line[colon_i + 1 :].strip()
        kind = KIND_CAPTURE
        if (m := _KIND_RE.match(function)) is not None:
            function, kind = m.group("fn"), m.group("kind")
            if kind != KIND_USAGE:
                raise ReportersError(
                    f"{FILENAME}:{lineno}: unknown sink kind [{kind}]"
                    f" (only [{KIND_USAGE}])"
                )
        if not file or not function or not reason:
            raise ReportersError(
                f"{FILENAME}:{lineno}: file, function and reason must be non-empty"
            )
        decls.append(
            Declaration(file=file, function=function, reason=reason, kind=kind)
        )
    return decls


def validate_paths(decls: list[Declaration], repo_root: Path) -> None:
    """Check each declared `<file>` exists inside the repo. Symbol existence
    is validated later by the resolving engine, not here."""
    root = repo_root.resolve()
    for d in decls:
        target = repo_root / d.file
        try:
            target.resolve().relative_to(root)
        except (ValueError, OSError):
            raise ReportersError(f"{FILENAME}: path escapes repo: {d.file}")
        if not target.is_file():
            raise ReportersError(f"{FILENAME}: no such file: {d.file}")
        if _ext(d.file) not in _KNOWN_EXTS:
            raise ReportersError(
                f"{FILENAME}: unsupported language (extension) for {d.file}"
            )
        if d.kind == KIND_USAGE and _ext(d.file) not in _GO_EXTS:
            # No engine enforces or validates non-Go usage sinks yet;
            # accepting one would be a silently dead line.
            raise ReportersError(
                f"{FILENAME}: [usage] sinks are Go-only (erclint ERC003): {d.file}"
            )


def load(repo_root: Path) -> list[Declaration]:
    """Parse + path-validate the repo-root file; absent file = no declarations."""
    path = repo_root / FILENAME
    if not path.is_file():
        return []
    decls = parse(path.read_text(encoding="utf-8"))
    validate_paths(decls, repo_root)
    return decls


def pairs(decls: list[Declaration]) -> tuple[tuple[str, str, str], ...]:
    """The (file, function, kind) transport tuple handed to engine argv builders."""
    return tuple((d.file, d.function, d.kind) for d in decls)


def _ext(path: str) -> str:
    dot = path.rfind(".")
    return path[dot:] if dot >= 0 else ""

#!/usr/bin/env python3
"""Check-only hygiene for dev.py lint (replaces the removed pre-commit hooks).

Four checks over the tracked tree: merge conflict markers, YAML parse, trailing
whitespace, and final newline. Checks, not fixers. Kept out of the `tackbox`
package (it stays the universal ERC/frontend linter); dev.py lint invokes this
via `uv run --with pyyaml` because the system python3 that runs dev.py has no
pyyaml, while dev.py itself must stay importable there.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import yaml

_CONFLICT_STARTS = ("<<<<<<< ", ">>>>>>> ", "||||||| ")


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _yaml_finding(rel: str, err: yaml.YAMLError) -> str:
    mark = getattr(err, "problem_mark", None)
    if mark is not None:
        return f"{rel}:{mark.line + 1}: invalid YAML: {err.problem}"
    return f"{rel}: invalid YAML: {err}"


def findings(rels: Iterable[str], root: Path) -> list[str]:
    out: list[str] = []
    for rel in rels:
        path = root / rel
        # symlinks (no deref), gitlinks, and index-only paths are not text to lint.
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\x00" in data:  # binary; text hygiene does not apply
            continue
        text = data.decode()
        if rel.endswith((".yml", ".yaml")):
            try:
                yaml.safe_load(text)
            except yaml.YAMLError as err:
                # no-report: collects a parse diagnostic into findings, no short-circuit
                out.append(_yaml_finding(rel, err))
        for i, raw in enumerate(text.split("\n"), 1):
            line = raw.rstrip("\r")  # tolerate CRLF without flagging the CR
            if line.startswith(_CONFLICT_STARTS) or line == "=======":
                out.append(f"{rel}:{i}: merge conflict marker")
            if line != line.rstrip(" \t"):
                out.append(f"{rel}:{i}: trailing whitespace")
        if data and not data.endswith(b"\n"):
            out.append(f"{rel}: missing final newline")
    return out


def main() -> int:
    root = Path(__file__).resolve().parent
    found = findings(tracked_files(root), root)
    for f in found:
        print(f, file=sys.stderr)
    if found:
        print(f"hygiene: {len(found)} issue(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

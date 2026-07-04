#!/usr/bin/env python3
"""tackbox dev script: lint / test / e2e / check (dev-script spec).

One entry point for the same checks locally and in CI. `check` = lint +
test and is what the pre-commit hook and CI run. Assumes the toolchain is
present (go, node + npm deps, opengrep, uv); it orchestrates, it does not
install.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

# uv invocation for the py test suite (mirrors the engine set installed in CI).
_PYTEST = ["uv", "run", "--directory", "py", "--with", "pytest", "--with", "pyyaml", "pytest", "-q"]


def _run(cmd: list[str]) -> int:
    return subprocess.run(cmd).returncode


def _aggregate(codes: list[int]) -> int:
    return next((c for c in codes if c != 0), 0)


def lint() -> int:
    # tackbox self-lint runs in-tree (dev mode), not `uvx tackbox@latest`: a
    # commit that changes a rule must be validated by that rule from the tree.
    return _aggregate(
        [
            _run(["go", "build", "./go/..."]),
            _run(["go", "vet", "./go/..."]),
            _run(["uv", "run", "--directory", "py", "python", "-m", "tackbox.cli", "lint", "."]),
            # Hygiene checks run under uv for pyyaml; the system python3 that runs
            # dev.py has none. hygiene.py stays out of the tackbox package.
            _run(["uv", "run", "--with", "pyyaml", "python", str(_ROOT / "hygiene.py")]),
        ]
    )


def test() -> int:
    # -count=1 disables the go test cache: golden tests build erclint via a
    # subprocess, so analyzer changes would not otherwise invalidate cached runs.
    return _aggregate(
        [
            _run(["go", "test", "-race", "-count=1", "./go/..."]),
            _run(["npm", "test"]),
            _run(_PYTEST),
        ]
    )


def e2e() -> int:
    print("no e2e tests found")
    return 0


def check() -> int:
    # Run both so one failing gate never hides the other; aggregate non-zero.
    return _aggregate([lint(), test()])


_COMMANDS = {"lint": lint, "test": test, "e2e": e2e, "check": check}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in _COMMANDS:
        print(f"usage: dev.py {{{'|'.join(_COMMANDS)}}}", file=sys.stderr)
        return 2
    return _COMMANDS[args[0]]()


if __name__ == "__main__":
    sys.exit(main())

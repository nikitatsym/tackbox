#!/usr/bin/env python3
"""tackbox dev script: lint / test / e2e / check (dev-script spec).

One entry point for the same checks locally and in CI. `check` = lint +
test and is what the pre-commit hook and CI run. Assumes the toolchain is
present (go, node + npm deps, opengrep, uv, java >= 17 + maven); it
orchestrates, it does not install.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

# Dirs never walked for suites: VCS, virtualenvs, vendored deps, build output,
# and engines/ (bundled third-party runtimes, not our tests). egg-info is pruned
# by suffix in _discover.
_PRUNE = {".git", ".venv", "venv", "node_modules", "build", "dist", "target", "engines", "__pycache__"}


def _run(cmd: list[str]) -> int:
    return subprocess.run(cmd).returncode


def _aggregate(codes: list[int]) -> int:
    return next((c for c in codes if c != 0), 0)


def _discover(name: str, root: Path) -> list[Path]:
    # Auto-discovery per the dev-script spec (no allow/deny list of suites). One
    # walk, prune noise in place (skips descending huge node_modules), sorted for
    # a deterministic runner order.
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE and not d.endswith(".egg-info")]
        if name in filenames:
            found.append(Path(dirpath) / name)
    return sorted(found)


def _python_test_dirs(root: Path = _ROOT) -> list[Path]:
    # A discovered pyproject without a tests/ dir is announced and skipped, never
    # silently dropped (the silent coverage hole this discovery closes).
    dirs: list[Path] = []
    for proj in _discover("pyproject.toml", root):
        d = proj.parent
        if (d / "tests").is_dir():
            dirs.append(d)
        else:
            print(f"dev.py: no tests/ in {d.relative_to(root)}, skipped")
    return dirs


def _pom_modules(pom: Path) -> list[str]:
    # <module> children of a reactor pom, read with the POM namespace stripped, so
    # a child a parent already builds is not also run standalone. Malformed xml
    # raises here rather than silently reading zero modules.
    tree = ET.parse(pom).getroot()
    return [el.text.strip() for el in tree.iter()
            if el.tag.rsplit("}", 1)[-1] == "module" and el.text and el.text.strip()]


def _maven_root_poms(root: Path = _ROOT) -> list[Path]:
    poms = _discover("pom.xml", root)
    children = {(pom.parent / mod).resolve() for pom in poms for mod in _pom_modules(pom)}
    return [p for p in poms if p.parent.resolve() not in children]


def _python_runners(root: Path = _ROOT) -> list[list[str]]:
    return [["uv", "run", "--directory", str(d), "--group", "dev", "pytest", "-q"]
            for d in _python_test_dirs(root)]


def _maven_runners(root: Path = _ROOT) -> list[list[str]]:
    return [["mvn", "-q", "-B", "-f", str(p), "verify"] for p in _maven_root_poms(root)]


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
    # Python and Maven suites are auto-discovered (dev-script spec), so a new
    # nested project or pom is covered with no edit here. `mvn verify` also builds
    # the shaded javalint.jar the thin wheel ships. go/npm self-discover in-tree.
    runners = [["go", "test", "-race", "-count=1", "./go/..."], ["npm", "test"]]
    runners += _python_runners()
    runners += _maven_runners()
    codes: list[int] = []
    for cmd in runners:
        print(f"dev.py: {shlex.join(cmd)}", flush=True)  # make each discovered suite visible
        codes.append(_run(cmd))
    return _aggregate(codes)


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

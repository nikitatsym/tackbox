#!/usr/bin/env python3
"""tackbox dev script: lint / test / e2e / check (dev-script spec).

One entry point for the same checks locally and in CI. `check` = lint +
test and is what the pre-commit hook and CI run. Host toolchains are assumed
present; repo-local dependency state (`node_modules`) is reproduced from the
lock on every run.
"""

from __future__ import annotations

import functools
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_bootstrapped = False

# Dirs never walked for suites: VCS, virtualenvs, vendored deps, build output,
# and engines/ (bundled third-party runtimes, not our tests). egg-info is pruned
# by suffix in _discover.
# .claude and .worktrees hold agent git worktrees (nested checkouts) that would
# otherwise be discovered as bogus duplicate suites.
_PRUNE = {".git", ".claude", ".worktrees", ".venv", "venv", "node_modules", "build", "dist", "target", "engines",
          "__pycache__"}

# The whole gate runs with UTF-8 text I/O: fixtures and engine output are
# UTF-8 by contract, and a cp1252 console (Windows runner default) must not
# leak its code page into pytest's own file writes or any python child.
os.environ["PYTHONUTF8"] = "1"


def _run(cmd: list[str], env: dict[str, str] | None = None) -> int:
    # Windows CreateProcess has no PATHEXT: resolve npm/mvn to their .cmd shims.
    exe = shutil.which(cmd[0])
    return subprocess.run([exe, *cmd[1:]] if exe else cmd, cwd=_ROOT, env=env).returncode


@functools.cache
def _repository_variables() -> frozenset[str]:
    out = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True
    ).stdout
    return frozenset(out.split())


def _fixture_env() -> dict[str, str]:
    # Fixture git must not address the checkout a hook exports (GIT_DIR, GIT_INDEX_FILE).
    return {name: value for name, value in os.environ.items() if name not in _repository_variables()}


def _checkout_env() -> dict[str, str] | None:
    # A linked-worktree hook exports GIT_DIR alone, which makes `uv run --directory py` the work tree.
    if "GIT_DIR" not in os.environ or "GIT_WORK_TREE" in os.environ:
        return None
    return {**os.environ, "GIT_WORK_TREE": str(_ROOT)}


def _bootstrap() -> int:
    global _bootstrapped
    if _bootstrapped:
        return 0
    cmd = ["npm", "ci", "--no-audit", "--no-fund"]
    print(f"dev.py: {shlex.join(cmd)}", flush=True)
    code = _run(cmd)
    if code != 0:
        return code
    _bootstrapped = True
    return 0


def _opengrep_pin() -> dict:
    system = sys.platform
    if system.startswith("linux"):
        system = "linux"
    elif system.startswith("win") or system == "cygwin":
        system = "windows"
    elif system == "darwin":
        system = "macos"
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    key = f"{system}-{machine}"
    manifest = json.loads((_ROOT / "engines" / "manifest.json").read_text())
    return manifest["platforms"][key]["opengrep"]


def _check_opengrep() -> int:
    pin = _opengrep_pin()
    pinned, source_url = pin["version"], pin["source_url"]
    executable = shutil.which("opengrep")
    found = "missing"
    if executable:
        result = subprocess.run(
            [executable, "--version"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            env=_fixture_env(),
        )
        if result.returncode != 0:
            print(f"dev.py: {executable} --version exited {result.returncode}", flush=True)
            if result.stderr.strip():
                print(result.stderr.rstrip(), flush=True)
            libraries = pin.get("archive_libraries", [])
            missing = [name for name in libraries if not (Path(executable).parent / name).is_file()]
            if missing:
                print(f"dev.py: missing beside it: {', '.join(missing)}", flush=True)
            repair = "extract the whole pinned ZIP" if libraries else "install"
            print(f"dev.py: {repair} from {source_url}", flush=True)
            return 2
        found = result.stdout.strip()
    if found == pinned:
        return 0
    print(
        f"dev.py: opengrep version mismatch: found {found}, pinned {pinned}; "
        f"install from {source_url}",
        flush=True,
    )
    return 2

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
    # `clean` first: surefire reads test resources from target/, and a fixture
    # deleted in source otherwise survives there as a phantom test case.
    return [["mvn", "-q", "-B", "-f", str(p), "clean", "verify"] for p in _maven_root_poms(root)]


def lint() -> int:
    code = _bootstrap()
    if code != 0:
        return code
    code = _check_opengrep()
    if code != 0:
        return code
    # tackbox self-lint runs in-tree (dev mode), not `uvx tackbox@latest`: a
    # commit that changes a rule must be validated by that rule from the tree.
    # Lint keeps a hook's GIT_INDEX_FILE: it checks the commit candidate.
    env = _checkout_env()
    return _aggregate(
        [
            _run(["go", "build", "./go/..."], env),
            _run(["go", "vet", "./go/..."], env),
            _run(["uv", "run", "--directory", "py", "python", "-m", "tackbox.cli", "lint", "."], env),
            # Hygiene checks run under uv for pyyaml; the system python3 that runs
            # dev.py has none. hygiene.py stays out of the tackbox package.
            _run(["uv", "run", "--with", "pyyaml", "python", str(_ROOT / "hygiene.py")], env),
        ]
    )


def test() -> int:
    code = _bootstrap()
    if code != 0:
        return code
    if sys.platform == "win32" and not shutil.which("gcc"):
        print(
            "dev.py: gcc is required on Windows for go test -race; "
            "install gcc and ensure it is on PATH",
            flush=True,
        )
        return 2
    # -count=1 disables the go test cache: golden tests build erclint via a
    # subprocess, so analyzer changes would not otherwise invalidate cached runs.
    # Python and Maven suites are auto-discovered (dev-script spec), so a new
    # nested project or pom is covered with no edit here. `mvn verify` also builds
    # the shaded javalint.jar the thin wheel ships. go/npm self-discover in-tree.
    runners = [_go_test_argv(), ["npm", "test"]]
    runners += _python_runners()
    runners += _maven_runners()
    codes: list[int] = []
    for cmd in runners:
        print(f"dev.py: {shlex.join(cmd)}", flush=True)  # make each discovered suite visible
        codes.append(_run(cmd, _fixture_env()))
    return _aggregate(codes)


def _go_test_argv() -> list[str]:
    return ["go", "test", "-race", "-count=1", "./go/..."]


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

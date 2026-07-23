"""Materialize the hermetic e2e fixture repo used by wheel tests and CI.

Writes a tiny git repo with one planted violation per engine into the
target directory (created if missing, must be empty). Inline constants
mirror steps 3-4 style so tackbox self-lint never encounters real files
on disk. Called by py/tests/test_wheels_e2e.py and by the wheels CI
matrix; keep it dependency-free so any Python 3.11+ runner can invoke it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


GO_MOD = """module e2e-fixture

go 1.21
"""

GO_ERC001 = """package pkga

import "errors"

func Do() {
\terr := errors.New("bad")
\tif err != nil {
\t\treturn
\t}
}
"""

# opengrep engine violation: exceptions-go (go-exit-in-recover). ERC006
# fingerprint moved to native erclint, so opengrep no longer fires on a bare
# capture call; this recover-then-exit is the surviving opengrep-owned rule.
GO_EXIT_IN_RECOVER = """package pkgb

import "os"

func Guard() {
\tdefer func() {
\t\tif v := recover(); v != nil {
\t\t\tos.Exit(2)
\t\t}
\t}()
}
"""

JS_SWALLOW = """try {
  doSomething()
} catch (e) {
}
"""

# JV001: catch swallows the exception. Exercises the hermetic `java -jar
# javalint.jar` path (the engine consumers run) end to end.
JAVA_SWALLOW = """class Handler {
    void run() {
        try {
            work();
        } catch (Exception e) {
        }
    }
}
"""

# Same JV001 shape, one directory deep - the wheels/CI matrix runs on Windows
# too, where a nested repo-relative path is the only way to exercise the
# engine's separator handling end to end (a root-level file never has one).
JAVA_SWALLOW_NESTED = """class Deep {
    void run() {
        try {
            work();
        } catch (Exception e) {
        }
    }
}
"""

# chars=ascii declares the check; the em-dash then trips declared-chars (MD-CHARS).
MD_NON_ASCII = "<!-- tackbox: chars=ascii -->\n# Title \u2014 dash goes here\n"


def materialize(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise SystemExit(f"target dir not empty: {root}")

    (root / "go.mod").write_text(GO_MOD)
    (root / "pkga").mkdir()
    (root / "pkga" / "violate.go").write_text(GO_ERC001)
    (root / "pkgb").mkdir()
    (root / "pkgb" / "recover.go").write_text(GO_EXIT_IN_RECOVER)
    (root / "swallow.js").write_text(JS_SWALLOW)
    (root / "Handler.java").write_text(JAVA_SWALLOW)
    (root / "javasub").mkdir()
    (root / "javasub" / "Deep.java").write_text(JAVA_SWALLOW_NESTED)
    (root / "notes.md").write_text(MD_NON_ASCII)

    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "fixture@tackbox")
    _git(root, "config", "user.name", "tackbox-fixture")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dir", type=Path, help="target directory (must be empty or nonexistent)")
    args = ap.parse_args()
    materialize(args.dir.resolve())
    print(f"materialized fixture in {args.dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

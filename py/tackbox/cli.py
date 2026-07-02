"""tackbox lint CLI entry point.

Dev mode: locates its own source tree relative to __file__ to find
the Go binaries and Node scripts. Step 5 will swap this for wheel-
bundled resources; the CLI shape (argv, exit codes, banner) stays.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import __version__
from .engines import (
    DEV_ENGINES,
    EngineResult,
    dispatch,
    parse_erclint_findings,
    resolve_dev_versions,
    run_engines,
)
from .source_set import (
    PathspecMagicError,
    filter_source_set,
    parse_ls_files_stage,
    parse_ls_files_untracked,
)

_BANNER_ORDER = ("erclint", "opengrep", "node", "eslint", "markdownlint")


def main(argv: list[str] | None = None) -> int:
    args = _parse_argv(sys.argv[1:] if argv is None else argv)
    if args.command != "lint":
        print(f"tackbox: unknown command {args.command!r}", file=sys.stderr)
        return 2
    try:
        return _run_lint(args.path)
    except PathspecMagicError as e:
        print(f"tackbox: {e}", file=sys.stderr)
        return 2


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="tackbox")
    sub = parser.add_subparsers(dest="command", required=True)
    lint = sub.add_parser("lint", help="lint the source set")
    lint.add_argument("path", nargs="?", default=".", help="scope path (default: .)")
    return parser.parse_args(argv)


def _run_lint(scope: str) -> int:
    repo_root = _find_repo_root()
    tackbox_root = Path(__file__).resolve().parents[2]

    files, warnings = _collect_source_set(repo_root, scope)
    for w in warnings:
        print(f"tackbox: warning: {w.reason}: {w.path}", file=sys.stderr)

    if not files:
        print(
            f"tackbox: scope {scope!r} matched no files in the source set",
            file=sys.stderr,
        )
        return 2

    _print_banner(tackbox_root)

    plan = dispatch(files, DEV_ENGINES)
    if not plan:
        return 0
    results = run_engines(plan, repo_root, tackbox_root)

    for r in results:
        sys.stdout.write(f"== {r.engine_id} ==\n")
        if r.stdout:
            sys.stdout.write(r.stdout)
            if not r.stdout.endswith("\n"):
                sys.stdout.write("\n")
        if r.stderr:
            sys.stderr.write(r.stderr)
            if not r.stderr.endswith("\n"):
                sys.stderr.write("\n")

    return _aggregate_exit(results)


def _aggregate_exit(results: list[EngineResult]) -> int:
    """Aggregate engine exit codes; promote erclint findings to nonzero.

    erclint in `-json` mode returns exit 0 even when findings exist -
    handover #2 pinned this contract. tackbox is the layer that translates
    findings into a failing aggregate exit.
    """
    max_code = 0
    for r in results:
        code = r.exit_code
        if code == 0 and r.engine_id == "erclint" and _erclint_has_findings(r.stdout):
            code = 1
        if code > max_code:
            max_code = code
    return max_code


def _erclint_has_findings(stdout: str) -> bool:
    try:
        return bool(parse_erclint_findings(stdout))
    except ValueError:
        # Analyzer-load errors surface as a failing aggregate.
        return True


def _collect_source_set(repo_root: Path, scope: str):
    stage_raw = subprocess.run(
        ["git", "ls-files", "-s", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    ).stdout
    untracked_raw = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    ).stdout
    return filter_source_set(
        parse_ls_files_stage(stage_raw),
        parse_ls_files_untracked(untracked_raw),
        scope,
        exists=lambda p: (repo_root / p).exists(),
        is_symlink=lambda p: (repo_root / p).is_symlink(),
    )


def _find_repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=True,
    )
    return Path(result.stdout.decode().strip())


def _print_banner(tackbox_root: Path) -> None:
    versions = resolve_dev_versions(tackbox_root)
    parts = " ".join(f"{k}={versions[k]}" for k in _BANNER_ORDER)
    print(f"tackbox {__version__} engines=dev {parts}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())

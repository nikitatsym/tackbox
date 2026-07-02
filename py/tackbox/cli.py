"""tackbox lint / doctor CLI entry point."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import __version__, cache, doctor
from .engines import (
    EngineResult,
    EngineSpec,
    active_engines,
    dispatch,
    engines_hash_hermetic,
    is_hermetic,
    parse_erclint_findings,
    resolve_dev_versions,
    resolve_hermetic_versions,
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
    if args.command == "lint":
        try:
            return _run_lint(args.path, no_cache=args.no_cache)
        except PathspecMagicError as e:
            print(f"tackbox: {e}", file=sys.stderr)
            return 2
    if args.command == "doctor":
        _print_banner(_tackbox_root())
        return doctor.run(sys.stdout)
    print(f"tackbox: unknown command {args.command!r}", file=sys.stderr)
    return 2


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="tackbox")
    sub = parser.add_subparsers(dest="command", required=True)
    lint = sub.add_parser("lint", help="lint the source set")
    lint.add_argument("path", nargs="?", default=".", help="scope path (default: .)")
    lint.add_argument(
        "--no-cache",
        action="store_true",
        help="ignore and do not write the (unit, engine) cache",
    )
    sub.add_parser("doctor", help="verify the hermetic install is functional")
    return parser.parse_args(argv)


def _tackbox_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run_lint(scope: str, no_cache: bool) -> int:
    repo_root = _find_repo_root()
    tackbox_root = _tackbox_root()

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

    plan = dispatch(files, active_engines())
    if not plan:
        return 0

    # Self-lint: tackbox lints itself. Cache is disabled so tackbox never
    # self-caches its own bugs (plan: "чтобы tackbox не самокэшировал").
    if tackbox_root.resolve() == repo_root.resolve():
        no_cache = True

    if no_cache:
        results = run_engines(plan, repo_root, tackbox_root)
    else:
        cache_root = cache.default_cache_root()
        engines_hash = engines_hash_hermetic() if is_hermetic() else cache.engines_hash_dev(tackbox_root)
        cache.gc_stale_engines(engines_hash, cache_root)

        filtered_plan, pending = _apply_cache(plan, repo_root, engines_hash, cache_root)
        results = run_engines(filtered_plan, repo_root, tackbox_root)
        _mark_clean_units(results, pending, engines_hash, cache_root)
        cache.gc_soft_cap(engines_hash, cache.SOFT_CAP, cache_root)

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


# -- Cache wiring ---------------------------------------------------------


def _apply_cache(
    plan: list[tuple[EngineSpec, list[str]]],
    repo_root: Path,
    engines_hash: str,
    cache_root: Path,
) -> tuple[list[tuple[EngineSpec, list[str]]], dict[str, dict]]:
    """Filter cached units out of each engine's args.

    Returns:
    - filtered_plan: engines that still have uncached args.
    - pending[engine_id] = {
        "arg_digest": [(arg, digest), ...],   # uncached args passed to engine
        "arg_ip": {arg: import_path, ...},     # erclint-only mapping
      }
      Used post-run to translate engine output into per-unit success and
      write markers for the clean units.
    """
    filtered_plan: list[tuple[EngineSpec, list[str]]] = []
    pending: dict[str, dict] = {}
    for engine, args in plan:
        arg_digest, extras = _digests_for_engine(engine, args, repo_root)
        uncached: list[tuple[str, str]] = []
        for arg, digest in arg_digest:
            if digest is None:
                uncached.append((arg, digest))
                continue
            key = cache.CacheKey(engines_hash, digest, engine.id)
            if not cache.is_cached(key, cache_root):
                uncached.append((arg, digest))
        pending[engine.id] = {"arg_digest": uncached, **extras}
        if uncached:
            filtered_plan.append((engine, [a for a, _ in uncached]))
    return filtered_plan, pending


def _digests_for_engine(
    engine: EngineSpec, args: list[str], repo_root: Path
) -> tuple[list[tuple[str, str]], dict]:
    if engine.id == "erclint":
        digest_map = cache.erclint_package_digests(repo_root, args)
        ip_map = cache.erclint_import_paths(repo_root, args)
        # digest None = lint always, cache never; dropping the arg instead
        # would silently skip linting the package.
        arg_digest = [(a, digest_map.get(a)) for a in args]
        return arg_digest, {"arg_ip": ip_map}
    arg_digest = [(a, cache.sha256_file(repo_root / a)) for a in args]
    return arg_digest, {}


def _mark_clean_units(
    results: list[EngineResult],
    pending: dict[str, dict],
    engines_hash: str,
    cache_root: Path,
) -> None:
    for r in results:
        info = pending.get(r.engine_id)
        if not info:
            continue
        clean_args = _clean_args(r, info)
        digest_of = dict(info["arg_digest"])
        for arg in clean_args:
            digest = digest_of.get(arg)
            if digest is None:
                continue
            cache.mark_clean(
                cache.CacheKey(engines_hash, digest, r.engine_id), cache_root
            )


def _clean_args(r: EngineResult, info: dict) -> list[str]:
    args = [a for a, _ in info["arg_digest"]]
    if r.engine_id == "erclint":
        try:
            findings = parse_erclint_findings(r.stdout)
        except ValueError:
            return []
        dirty_ips = {f.get("pkg") for f in findings}
        ip_map = info.get("arg_ip", {})
        # Unknown import path -> cannot attribute findings -> never clean.
        return [
            a for a in args
            if ip_map.get(a) is not None and ip_map[a] not in dirty_ips
        ]
    if r.exit_code == 0:
        return args
    return []


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
    if is_hermetic():
        versions = resolve_hermetic_versions()
        engines_id = f"sha256:{engines_hash_hermetic()}"
    else:
        versions = resolve_dev_versions(tackbox_root)
        engines_id = "dev"
    parts = " ".join(f"{k}={versions[k]}" for k in _BANNER_ORDER)
    print(f"tackbox {__version__} engines={engines_id} {parts}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())

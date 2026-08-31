"""tackbox lint / doctor CLI entry point."""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import stat
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from . import (
    __version__,
    approvals,
    cache,
    codequality,
    doctor,
    escapes,
    hookproto,
    proc,
    reporters,
    scopes,
)
from .engines import (
    EngineResult,
    EnginesStoreError,
    EngineSpec,
    Finding,
    active_engines,
    dispatch,
    engines_hash_hermetic,
    ensure_engines,
    erclint_base_import_path,
    erclint_compile_broken_pkgs,
    is_hermetic,
    iter_json_objects,
    lintable,
    located_findings,
    parse_erclint_findings,
    resolve_dev_versions,
    resolve_hermetic_versions,
    run_engines,
)
from .gitfiles import (
    AttributeResolutionError,
    collect_link_targets,
    collect_snapshot,
    resolve_attributes,
)
from .source_set import (
    EXCLUSION_ATTRIBUTES,
    PathspecMagicError,
    Snapshot,
    group_go_packages_by_module,
    narrow_files,
    parse_git_diff_names,
    parse_ls_files_untracked,
)


class ChangedScopeError(ValueError):
    """Raised when the git commands backing --changed / --since fail."""



class HookInfrastructureError(RuntimeError):
    """A recoverable dependency, process, or filesystem failure in the hook."""




class HookFileError(HookInfrastructureError):
    """A hook target could not be read or resolved safely."""


class HookRepositoryState(str, Enum):
    """The three states repository discovery can establish."""

    INACTIVE = "inactive"
    ACTIVE = "active"
    INFRASTRUCTURE_FAILURE = "infrastructure-failure"


@dataclass(frozen=True)
class HookRepository:
    """The repository root when known, plus its discovery state and detail."""

    state: HookRepositoryState
    root: Path | None = None
    reason: str = ""


@dataclass(frozen=True)
class HookPostScope:
    """Landed file scope and any uncertainty that must remain model-visible."""

    files: dict[str, set[int] | None]
    failures: tuple[str, ...] = ()

_BANNER_ORDER = ("erclint", "opengrep", "node", "eslint", "markdownlint")


def _is_closed_stdout(error: OSError) -> bool:
    if isinstance(error, BrokenPipeError):
        return True
    # Windows reports a no-reader pipe write as EINVAL instead of BrokenPipeError.
    # A pipe-write EINVAL carries no filename; a filesystem EINVAL does - without
    # that discriminator any EINVAL under piped stdout becomes a silent exit 141.
    return (
        os.name == "nt"
        and error.errno == errno.EINVAL
        and error.filename is None
        and error.filename2 is None
        and stat.S_ISFIFO(os.fstat(sys.stdout.fileno()).st_mode)
    )


def main(argv: list[str] | None = None) -> int:
    try:
        try:
            return _dispatch(argv)
        except OSError as error:
            if _is_closed_stdout(error):
                raise BrokenPipeError from error
            raise
    except BrokenPipeError:
        # no-report: downstream pipe closed (lint | head) - exit 141, no traceback
        # dup2 to devnull so the interpreter's atexit flush does not re-raise.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            # no-report: best-effort devnull redirect for the atexit flush; nothing to report
            pass
        return 141


def _dispatch(argv: list[str] | None) -> int:
    args = _parse_argv(sys.argv[1:] if argv is None else argv)
    if args.command == "lint":
        try:
            return _run_lint(
                args.path,
                no_cache=args.no_cache,
                changed=args.changed,
                since=args.since,
                codequality_path=args.codequality,
            )
        except (
            PathspecMagicError,
            ChangedScopeError,
            cache.GoListError,
            reporters.ReportersError,
            approvals.ApprovalsError,
            scopes.ScopesError,
            AttributeResolutionError,
            EnginesStoreError,
        ) as e:
            # no-report: CLI boundary: surface as message + exit 2; a traceback here is the bug
            print(f"tackbox: {e}", file=sys.stderr)
            return 2
    if args.command == "approvals":
        try:
            return _run_approvals(draft=args.draft)
        except (
            PathspecMagicError,
            reporters.ReportersError,
            approvals.ApprovalsError,
            scopes.ScopesError,
            AttributeResolutionError,
            subprocess.CalledProcessError,
        ) as e:
            # no-report: standalone approvals infra failure -> exit 1 (as everywhere)
            print(f"tackbox: {e}", file=sys.stderr)
            return 1
    if args.command == "escapes":
        # Inventory, not a gate (D013): exit 0 with entries or not; a bad --since
        # rev is exit 1 + one stderr line (handled inside run). _MARKER_RE is
        # injected so escapes stays a leaf (no cli<->escapes import cycle).
        try:
            return escapes.run(
                _find_repo_root(),
                since=args.since,
                context=args.context,
                marker_re=_MARKER_RE,
                out=sys.stdout,
                err=sys.stderr,
            )
        except AttributeResolutionError as e:
            # no-report: CLI boundary: a resolution failure is loud (exit 1), never a traceback
            print(f"tackbox: {e}", file=sys.stderr)
            return 1
    if args.command == "doctor":
        try:
            _print_banner(_tackbox_root())
            return doctor.run(sys.stdout)
        except (AttributeResolutionError, subprocess.CalledProcessError) as e:
            # no-report: CLI boundary: a resolution failure is loud (exit 1), never a traceback
            print(f"tackbox: {e}", file=sys.stderr)
            return 1
    if args.command == "hook":
        return _run_hook()
    if args.command == "hook-protocol":
        return _run_hook_protocol()
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
    lint.add_argument(
        "--changed",
        action="store_true",
        help="restrict to dirty tree (staged + unstaged + untracked)",
    )
    lint.add_argument(
        "--since",
        metavar="<ref>",
        default=None,
        help="restrict to three-dot diff <ref>...HEAD unioned with dirty tree",
    )
    lint.add_argument(
        "--codequality",
        metavar="<path>",
        default=None,
        help="also write a CodeClimate JSON report of all findings to <path>",
    )
    esc = sub.add_parser(
        "escapes", help="print the repo's bypass surface (markers, decls, lanes) as JSON"
    )
    esc.add_argument(
        "--since",
        metavar="<rev>",
        default=None,
        help="only entries new against <rev> by content identity (kind, file, text)",
    )
    esc.add_argument(
        "--context",
        metavar="N",
        type=int,
        default=3,
        help="source lines of context each side of an entry (default 3)",
    )
    appr = sub.add_parser(
        "approvals",
        help="check the suppression-marker approval manifest against the tree",
    )
    appr.add_argument(
        "--draft",
        action="store_true",
        help="emit draft manifest lines for uncovered markers (generator, not a gate)",
    )
    sub.add_parser("doctor", help="verify the hermetic install is functional")
    sub.add_parser(
        "hook",
        help="Claude Code hook: PostToolUse lint + approvals consistency, "
        "PreToolUse manifest gate",
    )
    sub.add_parser(
        "hook-protocol",
        help="host-neutral agent hook protocol v1: one JSON event on stdin, "
        "one JSON decision on stdout",
    )
    return parser.parse_args(argv)


def _tackbox_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _lint_results(
    repo_root: Path,
    tackbox_root: Path,
    scope: str,
    no_cache: bool,
    changed_scope: set[str] | None,
    snapshot: Snapshot | None = None,
    machine: bool = False,
):
    """Run the lint pipeline for `scope`; return (results, warnings, orphans).

    results is None when the scope matched no candidates (nothing to lint), []
    when candidates matched but no engine applies (an all-excluded scope lands
    here too), else the EngineResult list. `snapshot` is the whole-tree
    inventory; when omitted one is resolved. Prints nothing - callers own the
    banner / warning / findings output. Infra failures (PathspecMagicError,
    GoListError, ReportersError, AttributeResolutionError, git) propagate.
    """
    if snapshot is None:
        snapshot = collect_snapshot(repo_root)
    reporter_pairs = reporters.pairs(reporters.load(repo_root))
    policy = cache.policy_digest(reporter_pairs)

    # Narrow the whole-tree snapshot to this scope. Exit-2 ("matched no files")
    # is decided on the pre-exclusion candidate set, so a scope whose candidates
    # exist but are all attribute-excluded is a success (empty dispatch), not an
    # error; excluded files then leave the per-file engines' argv here.
    scope_candidates = narrow_files(snapshot.candidate_files(), scope, changed_scope)
    if not scope_candidates:
        return None, snapshot.warnings, []
    excluded = snapshot.excluded_files
    files = [f for f in scope_candidates if f not in excluded]

    plan = dispatch(files, active_engines())
    plan, go_orphans = _drop_go_orphans(plan, repo_root)
    if not plan:
        return [], snapshot.warnings, go_orphans

    # The Markdown link rule needs the whole-tree target inventory (built from the
    # raw git listing before source-set filtering, since that drops the symlinks /
    # gitlinks the inventory must record). Built only when mdlint is dispatched, so
    # a non-Markdown run pays no extra `git ls-files`.
    link_targets: tuple[tuple[str, str], ...] = ()
    if any(engine.wants_link_targets for engine, _ in plan):
        link_targets = tuple(collect_link_targets(repo_root))

    # Materialize the engine store once before the parallel run so worker
    # threads find it in place (dev mode has no store).
    if is_hermetic():
        ensure_engines()

    # Self-lint: tackbox lints itself. Cache is disabled so tackbox never
    # self-caches its own bugs.
    if tackbox_root.resolve() == repo_root.resolve():
        no_cache = True

    if no_cache:
        results = run_engines(plan, repo_root, tackbox_root, reporter_pairs, machine, link_targets)
    else:
        cache_root = cache.default_cache_root()
        engines_hash = engines_hash_hermetic() if is_hermetic() else cache.engines_hash_dev(tackbox_root)
        cache.gc_stale_engines(engines_hash, cache_root)

        filtered_plan, pending = _apply_cache(plan, repo_root, engines_hash, cache_root, policy)
        results = run_engines(filtered_plan, repo_root, tackbox_root, reporter_pairs, machine, link_targets)
        # Cache attribution reads RAW erclint truth, BEFORE the exclusion filter:
        # a mixed package whose only findings sit in excluded files is not marked
        # clean, so removing the attribute (content untouched) brings them back.
        _mark_clean_units(results, pending, engines_hash, cache_root)
        cache.gc_soft_cap(engines_hash, cache.SOFT_CAP, cache_root)

    # Pinned order: raw erclint result -> cache attribution (above) -> exclusion
    # filter -> console/codequality verdict. erclint compiles whole Go packages,
    # so a dispatched mixed package's excluded file is analyzed; its findings drop
    # here. A compile/type error is not filtered (the package cannot build without
    # the file) - it stays loud.
    results = _filter_excluded_findings(results, repo_root, excluded)
    return results, snapshot.warnings, go_orphans


def _run_lint(
    scope: str,
    no_cache: bool,
    changed: bool,
    since: str | None,
    codequality_path: str | None = None,
) -> int:
    repo_root = _find_repo_root()
    tackbox_root = _tackbox_root()

    changed_scope: set[str] | None = None
    if changed or since is not None:
        changed_scope = _compute_changed_scope(repo_root, since)

    # One inventory snapshot per command: lint (scope-narrowed) and the
    # whole-tree approvals predicate share it, so a scoped run performs ONE
    # attribute resolution, never a second check-attr.
    snapshot = collect_snapshot(repo_root)

    results, warnings, go_orphans = _lint_results(
        repo_root, tackbox_root, scope, no_cache, changed_scope, snapshot=snapshot
    )
    for w in warnings:
        print(f"tackbox: warning: {w.reason}: {w.path}", file=sys.stderr)
    if results is None:
        print(
            f"tackbox: scope {scope!r} matched no files in the source set",
            file=sys.stderr,
        )
        return 2

    _print_banner(tackbox_root)
    for pkg in go_orphans:
        print(
            f"tackbox: warning: no enclosing go.mod, skipped: {pkg}",
            file=sys.stderr,
        )

    exit_code = 0
    if results:
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

        # Flush inside the guarded region so a closed downstream pipe surfaces as
        # a caught BrokenPipeError (exit 141), not an interpreter-shutdown crash.
        sys.stdout.flush()
        exit_code = _aggregate_exit(results)

    # Scope-local, stateless: count excluded files the current scope touches, so
    # routine scoped runs are not wallpapered with a global constant. Absent when
    # the scope count is zero; the full inventory lives in `tackbox escapes`.
    excluded_in_scope = _excluded_in_scope(snapshot, scope, changed_scope)
    if excluded_in_scope:
        sys.stdout.write(
            f"excluded by attributes: {len(excluded_in_scope)} files in scope "
            "(tackbox escapes lists all)\n"
        )
        sys.stdout.flush()

    # The approvals predicate always covers the whole tree, regardless of lint
    # scope - a scope-following check would be a bypass for scoped CI. Its
    # inconsistencies count as findings (nonzero exit), same wall as the engines.
    report = _approvals_report(repo_root, snapshot=snapshot)
    for line in approvals.render_blocks(report):
        sys.stdout.write(line + "\n")
    sys.stdout.flush()
    if not report.ok():
        exit_code = max(exit_code, 1)

    # The report is the flag's purpose, so it is written regardless of exit_code.
    # located_findings needs machine-mode output, hence a second pass; the
    # console pass above stays byte-identical.
    if codequality_path is not None:
        findings = (
            _codequality_findings(
                repo_root, tackbox_root, scope, no_cache, changed_scope, snapshot
            )
            if results
            else []
        )
        appr_findings, fingerprints = _approvals_findings(report)
        codequality.write_report(
            Path(codequality_path), findings + appr_findings, fingerprints.get
        )
    return exit_code


def _approvals_report(repo_root: Path, snapshot: Snapshot | None = None) -> approvals.Report:
    """The whole-tree consistency report (D014/D015): resolve every marker to an
    address, load the manifest, and pair them. Always whole-tree. Runs over the
    snapshot's included files, so an attribute-excluded file's markers leave the
    inventory (D012 cascade) and a manifest entry addressing it orphans."""
    if snapshot is None:
        snapshot = collect_snapshot(repo_root)
    engines = active_engines()
    return approvals.check(
        repo_root, snapshot.included, _MARKER_RE, lambda rel: lintable(rel, engines)
    )


def _excluded_in_scope(
    snapshot: Snapshot, scope: str, changed_scope: set[str] | None
) -> list[str]:
    """Unique attribute-excluded files the current scope's candidates touch. The
    same narrow the lint uses, so the summary count and the excluded dispatch
    agree on the scope."""
    scope_candidates = narrow_files(snapshot.candidate_files(), scope, changed_scope)
    return sorted(set(scope_candidates) & snapshot.excluded_files)


def _approvals_findings(report: approvals.Report):
    """(findings, fingerprint map) for the codequality report. check_name is
    tackbox-approvals; location is the marker for uncovered / the manifest for
    orphans; fingerprint (via the override map) is the serialized entry address."""
    findings: list = []
    fingerprints: dict = {}
    for u in report.uncovered:
        f = Finding("tackbox-approvals", u.file, u.line, u.entry.line_text())
        findings.append(f)
        fingerprints[f] = u.entry.address
    for o in report.orphans:
        f = Finding("tackbox-approvals", approvals.FILENAME, o.line, o.entry.line_text())
        findings.append(f)
        fingerprints[f] = o.entry.address
    for path in report.unresolvable:
        f = Finding("tackbox-approvals", path, 1, "unresolvable file (syntax does not parse)")
        findings.append(f)
        fingerprints[f] = path
    return findings, fingerprints


def _run_approvals(draft: bool) -> int:
    """`tackbox approvals`: a thin consistency gate (0 consistent / 2
    inconsistent / 1 infra) that runs only the outline engine, not the lint
    engines. `--draft` is a generator, not a gate: it prints entry lines for
    uncovered markers and exits 0 unless unresolvable files make the draft
    incomplete (then 2)."""
    repo_root = _find_repo_root()
    report = _approvals_report(repo_root)
    if draft:
        for line in report.draft_lines():
            print(line)
        for o in report.orphans:
            print(f"orphan (no matching marker; remove?): {o.entry.line_text()}", file=sys.stderr)
        for path in report.unresolvable:
            print(f"unresolvable (syntax does not parse): {path}", file=sys.stderr)
        return 2 if report.unresolvable else 0
    for line in approvals.render_blocks(report):
        print(line)
    return 0 if report.ok() else 2


def _codequality_findings(
    repo_root: Path,
    tackbox_root: Path,
    scope: str,
    no_cache: bool,
    changed_scope: set[str] | None,
    snapshot: Snapshot | None = None,
) -> list:
    results, _warnings, _orphans = _lint_results(
        repo_root, tackbox_root, scope, no_cache, changed_scope,
        snapshot=snapshot, machine=True,
    )
    if not results:
        return []
    return _located(results, repo_root)


def _drop_go_orphans(
    plan: list[tuple[EngineSpec, list[str]]], repo_root: Path
) -> tuple[list[tuple[EngineSpec, list[str]]], list[str]]:
    """Drop package-mode args with no enclosing go.mod - loudly, upstream.

    erclint cannot lint a package outside any module; filtering here keeps
    the warning in one place and the engine/digest layers orphan-free.
    """
    filtered: list[tuple[EngineSpec, list[str]]] = []
    orphans: set[str] = set()
    for engine, args in plan:
        if engine.package_mode:
            groups, orphan = group_go_packages_by_module(
                args, lambda d: (repo_root / d / "go.mod").is_file()
            )
            orphans.update(orphan)
            args = sorted(p for pkgs in groups.values() for p in pkgs)
            if not args:
                continue
        filtered.append((engine, args))
    return filtered, sorted(orphans)


# -- Cache wiring ---------------------------------------------------------


def _apply_cache(
    plan: list[tuple[EngineSpec, list[str]]],
    repo_root: Path,
    engines_hash: str,
    cache_root: Path,
    policy: str,
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
        if not engine.cacheable:
            # Cross-file engine: always run the full arg set, and stay out of
            # pending so _mark_clean_units never writes a clean marker for it.
            filtered_plan.append((engine, args))
            continue
        arg_digest, extras = _digests_for_engine(engine, args, repo_root, policy)
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
    engine: EngineSpec, args: list[str], repo_root: Path, policy: str
) -> tuple[list[tuple[str, str]], dict]:
    if engine.id == "erclint":
        digest_map = cache.erclint_package_digests(repo_root, args, policy)
        ip_map = cache.erclint_import_paths(repo_root, args)
        # digest None = lint always, cache never; dropping the arg instead
        # would silently skip linting the package.
        arg_digest = [(a, digest_map.get(a)) for a in args]
        return arg_digest, {"arg_ip": ip_map}
    arg_digest = [
        (a, cache.non_go_unit_digest(a, cache.sha256_file(repo_root / a), policy))
        for a in args
    ]
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
            # No pending entry: nothing ran uncached, or a non-cacheable engine
            # (_apply_cache keeps it out) - either way, write no clean marker.
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
        if r.exit_code != 0:
            # no-report: crashed run never produced json -> attribute nothing, never a false clean
            return []
        try:
            findings = parse_erclint_findings(r.stdout)
        except ValueError:
            # no-report: unparseable erclint json -> attribute nothing, never a false clean
            return []
        # erclint keys a test-file finding under a `.test` package variant
        # (`pkg [pkg.test]`, `pkg_test [pkg.test]`), while arg_ip holds bare
        # import paths - so normalize every finding key to its base package or a
        # test-file finding would never match and the package would cache clean.
        dirty_ips = {erclint_base_import_path(f.get("pkg", "")) for f in findings}
        ip_map = info.get("arg_ip", {})
        # Unknown import path -> cannot attribute findings -> never clean.
        return [
            a for a in args
            if ip_map.get(a) is not None and ip_map[a] not in dirty_ips
        ]
    if r.engine_id == "javalint":
        if r.exit_code != 0:
            # no-report: nonzero = reporter-resolution crash, no json -> attribute nothing
            return []
        # attribute per file: each finding's outer JSON key is the repo-relative
        # file (the arg verbatim), so a file with a finding is never cached clean.
        try:
            findings = parse_erclint_findings(r.stdout)
        except ValueError:
            # no-report: unparseable javalint json -> attribute nothing, never a false clean
            return []
        dirty_files = {f.get("pkg") for f in findings}
        return [a for a in args if a not in dirty_files]
    if r.exit_code == 0:
        return args
    return []


# -- attribute-exclusion post-filter (erclint only) -----------------------


def _filter_excluded_findings(
    results: list[EngineResult], repo_root: Path, excluded: frozenset[str]
) -> list[EngineResult]:
    """Drop findings located in attribute-excluded files. Only erclint needs it:
    it is the sole package-mode engine, so it compiles a dispatched mixed
    package's excluded neighbor; every per-file engine already had the excluded
    files removed from its argv at dispatch. A compile/type error (payload
    `{"error": ...}`) is not a located finding and stays - an excluded file that
    breaks its dispatched package still fails loudly."""
    if not excluded:
        return results
    return [
        _filter_erclint_result(r, repo_root, excluded) if r.engine_id == "erclint" else r
        for r in results
    ]


def _filter_erclint_result(
    r: EngineResult, repo_root: Path, excluded: frozenset[str]
) -> EngineResult:
    """erclint's -json tree with excluded-file findings removed. Byte-identical
    when nothing is dropped (the common no-exclusion path never reparses)."""
    # JSON escapes Windows separators; inspect both serialized spellings.
    if not any(
        ef in r.stdout or ef.replace("/", "\\\\") in r.stdout for ef in excluded
    ):
        return r
    changed = False
    kept_objs: list[dict] = []
    for obj in iter_json_objects(r.stdout):
        kept_pkgs: dict = {}
        for pkg, analyzers in obj.items():
            kept_analyzers: dict = {}
            for analyzer, payload in analyzers.items():
                if not isinstance(payload, list):
                    kept_analyzers[analyzer] = payload  # {"error": ...} stays loud
                    continue
                kept = [
                    it for it in payload if not _posn_excluded(it, repo_root, excluded)
                ]
                if len(kept) != len(payload):
                    changed = True
                if kept:
                    kept_analyzers[analyzer] = kept
            if kept_analyzers:
                kept_pkgs[pkg] = kept_analyzers
        if kept_pkgs:
            kept_objs.append(kept_pkgs)
    if not changed:
        return r
    stdout = "".join(json.dumps(o, indent="\t") + "\n" for o in kept_objs)
    return EngineResult(r.engine_id, r.exit_code, stdout, r.stderr)


def _posn_excluded(item: dict, repo_root: Path, excluded: frozenset[str]) -> bool:
    posn = item.get("posn") or ""
    # erclint posn is `abs/path:line:col`; rsplit keeps a Windows drive colon.
    path = posn.rsplit(":", 2)[0]
    if not path:
        return False
    # Same lexical relpath as erclint_located_findings; erclint posns sit under
    # the repo root, so no cross-drive ValueError arises in practice.
    rel = os.path.relpath(path, repo_root).replace(os.sep, "/")
    return rel in excluded


_JSON_FINDING_ENGINES = frozenset({"erclint", "javalint"})


def _aggregate_exit(results: list[EngineResult]) -> int:
    """Aggregate engine exit codes; promote erclint/javalint findings to nonzero.

    Both emit findings as JSON and exit 0 regardless (erclint's `-json` mode,
    handover #2; javalint mirrors it). tackbox is the layer that translates
    those findings into a failing aggregate exit.
    """
    max_code = 0
    for r in results:
        code = r.exit_code
        if (
            code == 0
            and r.engine_id in _JSON_FINDING_ENGINES
            and _erclint_has_findings(r.stdout)
        ):
            code = 1
        if code > max_code:
            max_code = code
    return max_code


def _erclint_has_findings(stdout: str) -> bool:
    try:
        return bool(parse_erclint_findings(stdout))
    except ValueError:
        # no-report: unparseable erclint output -> failing aggregate, never a false clean
        return True


def _compute_changed_scope(repo_root: Path, since: str | None) -> set[str]:
    """Union of dirty tree with (optional) three-dot diff against <since>.

    Dirty tree = files that differ from HEAD in the index or worktree,
    plus untracked. Three-dot diff = files changed on this branch since
    the merge-base with <since>; matches the PR-style question "what did
    I change on my branch." A two-dot diff would leak reverse-changes
    when <since> progresses after fork.
    """
    scope: set[str] = set()
    completed = proc.run_bytes(
        ["git", "diff", "--name-only", "-z", "HEAD"],
        cwd=repo_root,
        capture_output=True,
    )
    if completed.returncode != 0:
        # Fresh repo without any commits: HEAD does not resolve. Fail with a
        # clean tackbox message instead of a Python traceback on onboarding.
        err = proc.decode(completed.stderr).strip()
        raise ChangedScopeError(
            f"--changed / --since requires at least one commit ({err})"
        )
    scope.update(parse_git_diff_names(completed.stdout))
    untracked = proc.run_bytes(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    ).stdout
    scope.update(parse_ls_files_untracked(untracked))
    if since is not None:
        completed = proc.run_bytes(
            ["git", "diff", "--name-only", "-z", f"{since}...HEAD"],
            cwd=repo_root,
            capture_output=True,
        )
        if completed.returncode != 0:
            err = proc.decode(completed.stderr).strip()
            raise ChangedScopeError(f"--since={since}: {err or 'git diff failed'}")
        scope.update(parse_git_diff_names(completed.stdout))
    return scope


def _find_repo_root() -> Path:
    result = proc.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=True,
    )
    return Path(result.stdout.strip())


def _print_banner(tackbox_root: Path) -> None:
    if is_hermetic():
        versions = resolve_hermetic_versions()
        engines_id = f"sha256:{engines_hash_hermetic()}"
    else:
        versions = resolve_dev_versions(tackbox_root)
        engines_id = "dev"
    parts = " ".join(f"{k}={versions[k]}" for k in _BANNER_ORDER)
    print(f"tackbox {__version__} engines={engines_id} {parts}", file=sys.stderr)


# -- Agent hook -----------------------------------------------------------
#
# Two hosts, one core. `tackbox hook` speaks Claude Code's event JSON and its
# exit-code contract; `tackbox hook-protocol` speaks the versioned host-neutral
# protocol in hookproto. Both normalize into one `hookproto.Event`, run the same
# gates and lint arms, then render the closed semantic outcome for their host.

_HOOK_TOOLS = frozenset({"Edit", "Write", "MultiEdit"})
# Suppression markers: the pattern the approvals check (scopes / approvals)
# matches against the tree inventory. The markdown chars marker is not a
# suppression (D017), so it is deliberately absent here.
_MARKER_RE = re.compile(
    r"(?:no-report|parse-skip|nil-return|long-comment|test-skip|dup-ok):"
)

# A gated file whose additions the host could not enumerate. The gate cannot
# decide, so it asks - never a silent allow (the whole point of the wall).
_UNCLASSIFIED = (
    "cannot classify what this edit adds (register paste, move, or unrecognized"
    " operation): {rel}"
)


def _run_hook() -> int:
    """Dispatch a Claude Code hook event read as JSON from stdin.

    Unknown / missing event -> exit 0 (forward-compat: never break another
    hook consumer). Unreadable stdin / bad JSON -> exit 1 + one stderr line
    (non-blocking). No version banner in hook mode.
    """
    event = _hook_stdin_payload()
    if event is None:
        return 1
    name = event.get("hook_event_name")
    if name == "PreToolUse":
        return _hook_pre(event)
    if name == "PostToolUse":
        return _hook_post(event)
    return 0


def _run_hook_protocol() -> int:
    """Dispatch one host-neutral protocol event and render one wire decision."""
    payload = _hook_stdin_payload()
    if payload is None:
        return 1
    try:
        event = hookproto.parse_request(payload)
    except hookproto.HookProtocolError as e:
        # no-report: malformed protocol has no safe phase to classify
        print(f"tackbox hook: {e}", file=sys.stderr)
        return 1
    outcome = _hook_event_outcome(_hook_repository(event.cwd), event)
    print(hookproto.render_decision(outcome, event.phase))
    return 0


def _hook_stdin_payload() -> dict | None:
    """The hook event JSON object on stdin, or None after one stderr line."""
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("hook event is not a JSON object")
    except (json.JSONDecodeError, ValueError, OSError) as e:
        # no-report: raw stdin boundary emits the documented one-line diagnostic
        print(f"tackbox hook: unreadable stdin: {e}", file=sys.stderr)
        return None
    return payload


def _hook_repository(cwd: str | None) -> HookRepository:
    """Discover inactive, active, and broken hook roots without conflating them."""
    try:
        return _discover_hook_repository(cwd)
    except HookInfrastructureError as e:
        # no-report: repository discovery returns its explicit three-state result
        return HookRepository(HookRepositoryState.INFRASTRUCTURE_FAILURE, reason=str(e))


def _discover_hook_repository(cwd: str | None) -> HookRepository:
    """Discover a hook root, raising typed failures for the state boundary."""
    if not isinstance(cwd, str) or not cwd:
        raise HookInfrastructureError("event cwd is missing")
    cwd_path = Path(cwd)
    if not cwd_path.is_absolute():
        raise HookInfrastructureError(f"event cwd is not absolute: {cwd!r}")
    git_env = os.environ | {"LC_ALL": "C", "LANG": "C"}
    try:
        result = proc.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            env=git_env,
        )
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        proc.ChildStreamError,
    ) as e:
        raise HookInfrastructureError(f"cannot discover hook repository: {e}") from e
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "not a git repository" in stderr:
            return HookRepository(HookRepositoryState.INACTIVE)
        detail = stderr or result.stdout.strip() or f"exit {result.returncode}"
        raise HookInfrastructureError(
            f"cannot discover hook repository: git rev-parse failed: {detail}"
        )
    root_text = result.stdout.strip()
    if not root_text:
        raise HookInfrastructureError("cannot parse hook repository root: empty output")
    try:
        root = Path(root_text)
    except ValueError as e:
        raise HookInfrastructureError(
            f"cannot parse hook repository root: {e}"
        ) from e
    if not root.is_absolute():
        raise HookInfrastructureError(
            f"cannot parse hook repository root: relative root {root_text!r}"
        )
    try:
        root_exists = root.is_dir()
        root_has_dev_py = (root / "dev.py").is_file()
    except (OSError, ValueError) as e:
        raise HookInfrastructureError(f"cannot inspect hook repository: {e}") from e
    if not root_exists:
        raise HookInfrastructureError(f"git rev-parse returned a missing root: {root}")
    if not root_has_dev_py:
        return HookRepository(HookRepositoryState.INACTIVE, root=root)
    return HookRepository(HookRepositoryState.ACTIVE, root=root)


def _hook_event_outcome(
    repository: HookRepository, event: hookproto.Event
) -> hookproto.Outcome:
    """Render every typed hook infrastructure failure as an unverified outcome."""
    try:
        outcome = _hook_event_outcome_inner(repository, event)
    except HookInfrastructureError as e:
        # no-report: shared host boundary renders typed infrastructure as unverified
        outcome = hookproto.Outcome(
            hookproto.OutcomeKind.UNVERIFIED,
            f"tackbox hook: {e}",
        )
    return _post_unverified_outcome(event, outcome)


def _post_unverified_outcome(
    event: hookproto.Event, outcome: hookproto.Outcome
) -> hookproto.Outcome:
    """Add the retry-safety facts once before either host renders a post warning."""
    if event.phase != hookproto.POST or outcome.kind is not hookproto.OutcomeKind.UNVERIFIED:
        return outcome
    return hookproto.Outcome(
        hookproto.OutcomeKind.UNVERIFIED,
        "\n".join((
            "The mutation may already have landed.",
            f"Tackbox verification did not complete: {outcome.reason}",
            "Do not repeat the mutation; dev.py check remains required.",
        )),
    )

def _hook_event_outcome_inner(
    repository: HookRepository, event: hookproto.Event
) -> hookproto.Outcome:
    """Run the shared semantic core after repository discovery."""
    if repository.state is HookRepositoryState.INFRASTRUCTURE_FAILURE:
        return hookproto.Outcome(hookproto.OutcomeKind.UNVERIFIED, repository.reason)
    if repository.state is HookRepositoryState.INACTIVE:
        removed_root_dev_py = (
            event.phase == hookproto.POST
            and repository.root is not None
            and _removes_root_dev_py(repository.root, event)
        )
        if removed_root_dev_py:
            return hookproto.Outcome(
                hookproto.OutcomeKind.UNVERIFIED,
                "root dev.py was removed or moved",
            )
        return hookproto.Outcome(hookproto.OutcomeKind.INACTIVE)
    assert repository.root is not None
    if event.phase == hookproto.PRE:
        return _hook_pre_decision(repository.root, event)
    return _hook_post_decision(repository.root, event)


# -- Claude Code adapter --------------------------------------------------


def _claude_event(phase: str, event: dict) -> hookproto.Event:
    """Normalize Claude's edit metadata into the shared policy event."""
    tool = event.get("tool_name")
    cwd = event.get("cwd")
    if not isinstance(tool, str):
        return hookproto.Event(
            phase=phase,
            cwd=cwd,
            tool="",
            unknown="Claude Code supplied a non-string tool name",
        )
    if tool not in _HOOK_TOOLS:
        return hookproto.Event(phase=phase, cwd=cwd, tool=tool)
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return hookproto.Event(
            phase=phase,
            cwd=cwd,
            tool=tool,
            unknown="Claude Code supplied a non-object tool input",
        )
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return hookproto.Event(
            phase=phase,
            cwd=cwd,
            tool=tool,
            unknown="Claude Code supplied a non-empty string file path",
        )
    try:
        target = _claude_target(Path(file_path), tool, tool_input, cwd)
    except hookproto.HookProtocolError as e:
        # no-report: malformed Claude tool input must deny before execution
        return hookproto.Event(
            phase=phase,
            cwd=cwd,
            tool=tool,
            unknown=f"Claude Code supplied malformed {tool} input: {e}",
        )
    return hookproto.Event(phase=phase, cwd=cwd, tool=tool, targets=(target,))


def _claude_target(
    path: Path, tool: str, tool_input: dict, cwd: object
) -> hookproto.Target:
    """Validate Claude fields through the same strict protocol as OMP."""
    if tool == "Write":
        target = {
            "path": str(path),
            "op": hookproto.WRITE,
            "expectedPresent": True,
            "content": _claude_string(tool_input, "content", "Write"),
        }
        protocol_tool = hookproto.WRITE
    elif tool == "MultiEdit":
        edits = tool_input.get("edits", [])
        if not isinstance(edits, list):
            raise hookproto.HookProtocolError("Claude Code MultiEdit edits must be a list")
        added: list[str] = []
        removed: list[str] = []
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                raise hookproto.HookProtocolError(
                    f"Claude Code MultiEdit edits[{index}] must be an object"
                )
            added.append(_claude_string(edit, "new_string", f"MultiEdit edits[{index}]"))
            removed.append(_claude_string(edit, "old_string", f"MultiEdit edits[{index}]"))
        target = {
            "path": str(path),
            "op": hookproto.EDIT,
            "expectedPresent": True,
            "added": added,
            "removed": removed,
        }
        protocol_tool = hookproto.EDIT
    else:
        target = {
            "path": str(path),
            "op": hookproto.EDIT,
            "expectedPresent": True,
            "added": [_claude_string(tool_input, "new_string", "Edit")],
            "removed": [_claude_string(tool_input, "old_string", "Edit")],
        }
        protocol_tool = hookproto.EDIT
    request = {
        "protocol": hookproto.VERSION,
        "phase": hookproto.PRE,
        "cwd": cwd,
        "tool": protocol_tool,
        "targets": [target],
    }
    return hookproto.parse_request(request).targets[0]


def _claude_string(values: dict, key: str, label: str) -> str:
    """Return an optional Claude string while rejecting a supplied non-string."""
    if key not in values:
        return ""
    value = values[key]
    if not isinstance(value, str):
        raise hookproto.HookProtocolError(f"Claude Code {label}.{key} must be a string")
    return value


def _hook_pre(event: dict) -> int:
    """Render the shared Pre outcome through Claude Code's permission surface."""
    outcome = _hook_event_outcome(
        _hook_repository(event.get("cwd")),
        _claude_event(hookproto.PRE, event),
    )
    if outcome.kind is hookproto.OutcomeKind.APPROVAL_REQUIRED:
        return _hook_ask_reason(outcome.reason)
    if outcome.kind in {
        hookproto.OutcomeKind.VIOLATION,
        hookproto.OutcomeKind.UNVERIFIED,
    }:
        return _hook_deny_reason(outcome.reason)
    return 0


def _hook_post(event: dict) -> int:
    """Render a landed Claude mutation without hiding policy violations."""
    normalized = _claude_event(hookproto.POST, event)
    outcome = _hook_event_outcome(
        _hook_repository(event.get("cwd")),
        normalized,
    )
    if outcome.kind is hookproto.OutcomeKind.UNVERIFIED:
        return _hook_unverified(outcome)
    if outcome.kind is not hookproto.OutcomeKind.VIOLATION:
        return 0
    if not normalized.targets:
        print(json.dumps({"decision": "block", "reason": outcome.reason}))
        return 0
    for line in outcome.reason.splitlines():
        sys.stderr.write(line + "\n")
    return 2


def _hook_unverified(outcome: hookproto.Outcome) -> int:
    """Write a non-blocking post diagnostic without treating it as a policy hit."""
    if outcome.kind is not hookproto.OutcomeKind.UNVERIFIED:
        return 0
    text = outcome.reason
    sys.stderr.write(text if text.endswith("\n") else text + "\n")
    return 1


def _hook_ask_reason(reason: str) -> int:
    return _hook_permission_reason("ask", reason)


def _hook_deny_reason(reason: str) -> int:
    return _hook_permission_reason("deny", reason)


def _hook_permission_reason(decision: str, reason: str) -> int:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


# -- Pre phase: the approval gates ----------------------------------------


def _hook_pre_decision(
    root: Path, event: hookproto.Event
) -> hookproto.Outcome:
    """Classify a Pre mutation; typed policy failures reach the shared boundary."""
    if event.unknown:
        return hookproto.Outcome(hookproto.OutcomeKind.UNVERIFIED, event.unknown)
    reasons = _pre_gate_reasons(root, event)
    if reasons:
        return hookproto.Outcome(
            hookproto.OutcomeKind.APPROVAL_REQUIRED,
            "\n".join(reasons),
        )
    return hookproto.Outcome(hookproto.OutcomeKind.ALLOW)


def _pre_gate_reasons(root: Path, event: hookproto.Event) -> list[str]:
    """Return every user-approvable gate in deterministic target order."""
    reasons: list[str] = []
    ordinary: list[str] = []
    for target in event.targets:
        if _target_removes_root_dev_py(root, target):
            reasons.append(
                "remove or move root dev.py (disables the tackbox hook): dev.py"
            )
        gate = _gated_file(root, target.path)
        if gate is not None:
            reason = _named_gate_ask(gate, root, target)
            if reason is not None:
                reasons.append(reason)
            continue
        rel = _hook_rel_strict(target.path, root)
        if rel is not None:
            ordinary.append(rel)
    if not ordinary:
        return reasons
    try:
        attrs = resolve_attributes(root, ordinary)
    except (
        AttributeResolutionError,
        OSError,
        subprocess.SubprocessError,
        proc.ChildStreamError,
    ) as e:
        raise HookInfrastructureError(f"cannot resolve target attributes: {e}") from e
    for rel in ordinary:
        excluded = attrs.get(rel)
        if excluded:
            reasons.append(
                f"edit attribute-excluded file ({', '.join(excluded)}): {rel}"
            )
    return reasons


def _removes_root_dev_py(root: Path, event: hookproto.Event) -> bool:
    """True when an event declares the removal or move of the hook root."""
    return any(_target_removes_root_dev_py(root, target) for target in event.targets)


def _target_removes_root_dev_py(root: Path, target: hookproto.Target) -> bool:
    """True when one declared source mutation would disable an active hook root."""
    return (
        target.operation in {hookproto.DELETE, hookproto.MOVE}
        and not target.expected_present
        and _same_path(target.path, root / "dev.py")
    )


def _gated_file(root: Path, path: Path) -> str | None:
    """Which named gate owns `path` - the approval manifest, the reporters file, or
    a `.gitattributes` - or None for an ordinary file. Attributes govern their
    subtree, so a `.gitattributes` in any directory gates; root-only would be a
    hole. The two `.tackbox/` files are root-only: a same-named file elsewhere
    does not participate."""
    if _same_path(path, root / approvals.FILENAME):
        return approvals.FILENAME
    if _same_path(path, root / reporters.FILENAME):
        return reporters.FILENAME
    if path.name == ".gitattributes":
        return ".gitattributes"
    return None


def _named_gate_ask(
    gate: str, root: Path, target: hookproto.Target
) -> str | None:
    """The ask for one named gate, or None when the change is free - a removal, or
    a line that sets no honored attribute."""
    if target.ambiguous:
        return _UNCLASSIFIED.format(rel=_hook_rel(target.path, root))
    added = _pre_added(target)
    if gate == approvals.FILENAME:
        return _manifest_ask(added)
    if gate == reporters.FILENAME:
        line = _reporters_added_line(added)
        return None if line is None else f".tackbox/reporters line added: {line} ({gate})"
    return _gitattributes_exclusion_ask(added)


def _pre_added(target: hookproto.Target) -> Counter[str]:
    """Return trim-normalized additions, failing closed when a full read fails."""
    if target.content is None:
        old, new = "\n".join(target.removed), "\n".join(target.added)
        return _line_counter(new) - _line_counter(old)
    try:
        old = target.path.read_text(encoding="utf-8") if target.path.is_file() else ""
    except (OSError, UnicodeDecodeError) as e:
        raise HookFileError(
            f"cannot read {target.path} before full replacement: {e}"
        ) from e
    return _line_counter(target.content) - _line_counter(old)


def _line_counter(text: str) -> Counter[str]:
    return Counter(s.strip() for s in text.splitlines() if s.strip())


def _manifest_ask(added: Counter[str]) -> str | None:
    """The manifest approval ask for the added `.tackbox/approvals` lines, or None
    when the change adds no entry line (removals are free).

    One call adding several entries draws ONE ask (the permission is per-call and
    indivisible - approved or rejected atomically). Duplicate entries collapse to
    one line with ` x<count>`; the header counts total occurrences. Entries are
    listed in deterministic lexicographic line order."""
    if not added:
        return None
    ordered = sorted(added)
    total = sum(added.values())
    if total == 1:
        return f"approve suppression marker: {ordered[0]}"
    header = (
        f"approve {total} suppression markers (Allow = all, Deny = none;"
        " re-add one by one to decide individually):"
    )
    body = [
        f"  {line}" + (f" x{added[line]}" if added[line] > 1 else "") for line in ordered
    ]
    return "\n".join([header, *body])


def _reporters_added_line(added: Counter[str]) -> str | None:
    """The first added `.tackbox/reporters` line, or None when the change only
    removes lines."""
    return next(added.elements(), None)


def _gitattributes_exclusion_ask(added: Counter[str]) -> str | None:
    """The ask for added `.gitattributes` line(s) positively setting one of the
    three honored attributes (bare `<attr>` or `<attr>=true`), or None when the
    change adds none. Removals, `=false`, `-attr`, `!attr`, and non-exclusion
    lines are free - only the widening direction gates. One call adding several
    such lines draws ONE joint ask (the permission is per-call and indivisible),
    lines listed in deterministic lexicographic order. Prediction is textual over
    the added lines, a superset, and recognizes literal attribute names only (a
    macro-referencing line is R2's plane)."""
    exclusions = Counter(
        {line: n for line, n in added.items() if _is_exclusion_line(line)}
    )
    if not exclusions:
        return None
    ordered = sorted(exclusions)
    total = sum(exclusions.values())
    if total == 1:
        return f".gitattributes exclusion line added: {ordered[0]}"
    header = (
        f"add {total} .gitattributes exclusion lines (Allow = all, Deny = none;"
        " re-add one by one to decide individually):"
    )
    body = [
        f"  {ln}" + (f" x{exclusions[ln]}" if exclusions[ln] > 1 else "")
        for ln in ordered
    ]
    return "\n".join([header, *body])


def _is_exclusion_line(line: str) -> bool:
    """True iff a `.gitattributes` line positively sets one of the three honored
    attributes for its pattern. The first whitespace token is the pattern; a later
    token that is a bare honored name or `<name>=true` sets it. `-name`, `!name`,
    `<name>=false`, a comment, and a non-honored attribute do not."""
    if line.startswith("#"):
        return False
    tokens = line.split()
    for tok in tokens[1:]:
        name, sep, value = tok.partition("=")
        if name not in EXCLUSION_ATTRIBUTES:
            continue
        if sep == "" or value == "true":
            return True
    return False


# -- Post phase: the consistency wall + the diff-scoped lint arm ----------


def _hook_post_decision(
    root: Path, event: hookproto.Event
) -> hookproto.Outcome:
    """Check the landed tree while keeping violations distinct from uncertainty."""
    snapshot, blocks = _hook_snapshot_and_blocks(root)
    if blocks:
        if event.unknown:
            blocks.append(f"verification uncertainty: tackbox hook: {event.unknown}")
        return hookproto.Outcome(hookproto.OutcomeKind.VIOLATION, "\n".join(blocks))
    if event.unknown:
        return hookproto.Outcome(
            hookproto.OutcomeKind.UNVERIFIED,
            f"tackbox hook: {event.unknown}",
        )
    if not event.succeeded and not event.targets:
        return hookproto.Outcome(hookproto.OutcomeKind.ALLOW)
    scope = _post_scope(root, event)
    if not scope.files:
        return _with_scope_failures(
            hookproto.Outcome(hookproto.OutcomeKind.ALLOW),
            scope,
        )
    try:
        results, _warnings, _orphans = _hook_lint_results(root, snapshot, scope.files)
    except HookInfrastructureError as e:
        raise _scoped_hook_error(e, scope) from e
    if results is None:
        return _with_scope_failures(
            hookproto.Outcome(hookproto.OutcomeKind.ALLOW),
            scope,
        )
    try:
        break_lines = _hook_compile_break(results)
    except ValueError as e:
        raise _scoped_hook_error(
            HookInfrastructureError(f"cannot parse linter output: {e}"),
            scope,
        ) from e
    if break_lines:
        return _with_scope_failures(
            hookproto.Outcome(
                hookproto.OutcomeKind.VIOLATION,
                "\n".join(break_lines),
            ),
            scope,
        )
    try:
        findings = _located(results, root)
    except ValueError as e:
        raise _scoped_hook_error(
            HookInfrastructureError(f"cannot parse linter output: {e}"),
            scope,
        ) from e
    if findings:
        return _with_scope_failures(_hook_findings_outcome(findings, scope.files), scope)
    return _with_scope_failures(_hook_infra_or_clean(results), scope)


def _scoped_hook_error(
    error: HookInfrastructureError, scope: HookPostScope
) -> HookInfrastructureError:
    """Attach prior target uncertainty before it reaches the shared boundary."""
    if not scope.failures:
        return error
    return HookInfrastructureError(
        f"{error}\nverification uncertainty: {'; '.join(scope.failures)}"
    )


def _hook_snapshot_and_blocks(root: Path) -> tuple[Snapshot, list[str]]:
    """Build the one shared snapshot used by the wall and the scoped lint arm."""
    try:
        snapshot = collect_snapshot(root)
        blocks = approvals.render_blocks(
            _approvals_report(root, snapshot=snapshot)
        )[1:]
    except (
        PathspecMagicError,
        cache.GoListError,
        reporters.ReportersError,
        approvals.ApprovalsError,
        scopes.ScopesError,
        AttributeResolutionError,
        EnginesStoreError,
        OSError,
        UnicodeDecodeError,
        subprocess.SubprocessError,
        proc.ChildStreamError,
    ) as e:
        raise HookInfrastructureError(f"cannot inspect the worktree: {e}") from e
    return snapshot, blocks


def _hook_lint_results(
    root: Path, snapshot: Snapshot, scope: dict[str, set[int] | None]
):
    """Run hook-scoped engines and translate known runtime dependencies."""
    try:
        return _lint_results(
            root,
            _tackbox_root(),
            ".",
            no_cache=False,
            changed_scope=set(scope),
            snapshot=snapshot,
            machine=True,
        )
    except (
        PathspecMagicError,
        ChangedScopeError,
        cache.GoListError,
        reporters.ReportersError,
        approvals.ApprovalsError,
        scopes.ScopesError,
        AttributeResolutionError,
        EnginesStoreError,
        OSError,
        UnicodeDecodeError,
        subprocess.SubprocessError,
        proc.ChildStreamError,
    ) as e:
        raise HookInfrastructureError(f"cannot lint landed files: {e}") from e


def _post_scope(root: Path, event: hookproto.Event) -> HookPostScope:
    """Build landed line scope while recording every failed verification edge."""
    files: dict[str, set[int] | None] = {}
    failures: list[str] = []
    for target in event.targets:
        if not target.expected_present:
            continue
        try:
            rel = _hook_rel_strict(target.path, root)
            if rel is None:
                continue
            affected, failure = _affected_for(target, rel)
        except HookFileError as e:
            # no-report: retain other targets while recording partial verification failure
            failures.append(str(e))
            continue
        if failure is not None:
            failures.append(failure)
        if rel not in files:
            files[rel] = affected
            continue
        prior = files[rel]
        files[rel] = None if prior is None or affected is None else prior | affected
    return HookPostScope(files, tuple(failures))


def _affected_for(
    target: hookproto.Target, rel: str
) -> tuple[set[int] | None, str | None]:
    """Return landed scope or raise a typed failure when it cannot be verified."""
    if not target.path.is_file():
        raise HookFileError(f"expected post file is absent: {rel}")
    try:
        text = target.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise HookFileError(f"cannot read expected post file {rel}: {e}") from e
    if target.content is not None or target.ambiguous:
        return None, None
    fragments = [fragment for fragment in target.added if fragment]
    if not fragments:
        return None, None
    lines, missing = _span_lines(text, fragments)
    if missing:
        return (
            None,
            f"added fragment was not found in landed {rel}; lint widened to the whole file",
        )
    return lines, None


def _span_lines(content: str, substrings: list[str]) -> tuple[set[int], bool]:
    """Locate every added fragment and report whether any fragment was absent."""
    lines: set[int] = set()
    missing = False
    for sub in substrings:
        if not sub:
            continue
        start = 0
        found = False
        while (idx := content.find(sub, start)) >= 0:
            found = True
            first = content.count("\n", 0, idx) + 1
            lines.update(range(first, first + sub.count("\n") + 1))
            start = idx + 1
        if not found:
            missing = True
    return lines, missing


def _with_scope_failures(
    outcome: hookproto.Outcome, scope: HookPostScope
) -> hookproto.Outcome:
    """Keep scope uncertainty visible without letting it hide a known violation."""
    if not scope.failures:
        return outcome
    detail = "\n".join(scope.failures)
    if outcome.kind is hookproto.OutcomeKind.VIOLATION:
        return hookproto.Outcome(
            hookproto.OutcomeKind.VIOLATION,
            f"{outcome.reason}\nverification uncertainty: {detail}",
        )
    if outcome.kind is hookproto.OutcomeKind.UNVERIFIED:
        return hookproto.Outcome(
            hookproto.OutcomeKind.UNVERIFIED,
            f"{outcome.reason}\n{detail}",
        )
    return hookproto.Outcome(hookproto.OutcomeKind.UNVERIFIED, detail)


def _hook_findings_outcome(
    findings: list, scope: dict[str, set[int] | None]
) -> hookproto.Outcome:
    """Findings on touched lines are violations; pre-existing findings are brief."""
    on_diff, elsewhere = _partition_by_scope(findings, scope)
    if not on_diff:
        return hookproto.Outcome(hookproto.OutcomeKind.ALLOW)
    lines = [_finding_line(finding) for finding in on_diff]
    if elsewhere:
        lines.append(f"{len(elsewhere)} pre-existing elsewhere (dev.py check enforces)")
    return hookproto.Outcome(hookproto.OutcomeKind.VIOLATION, "\n".join(lines))


def _partition_by_scope(
    findings: list, scope: dict[str, set[int] | None]
) -> tuple[list, list]:
    """Split findings into touched and pre-existing locations."""
    on_diff: list = []
    elsewhere: list = []
    for finding in findings:
        if finding.file is None:
            on_diff.append(finding)
        elif finding.file not in scope:
            elsewhere.append(finding)
        elif (
            (affected := scope[finding.file]) is None
            or finding.line is None
            or finding.line in affected
        ):
            on_diff.append(finding)
        else:
            elsewhere.append(finding)
    return on_diff, elsewhere


def _finding_line(finding) -> str:
    """Render one stable hook finding line."""
    loc = (
        f"{finding.file}:{finding.line}"
        if finding.line is not None
        else (finding.file or "?")
    )
    if finding.message:
        return f"{loc}: {finding.rule}: {' '.join(finding.message.split())}"
    return f"{loc}: {finding.rule}"


def _located(results: list, root: Path) -> list:
    return [
        finding
        for result in results
        for finding in located_findings(result.engine_id, result.stdout, root)
    ]


def _hook_infra_or_clean(results: list) -> hookproto.Outcome:
    """Treat a nonzero engine without findings as unverified, never as clean."""
    if _aggregate_exit(results) == 0:
        return hookproto.Outcome(hookproto.OutcomeKind.ALLOW)
    parts = [
        result.stderr.rstrip()
        for result in results
        if result.exit_code != 0 and result.stderr.strip()
    ]
    detail = "\n".join(parts) or "a linter exited nonzero without a diagnostic"
    return hookproto.Outcome(
        hookproto.OutcomeKind.UNVERIFIED,
        f"tackbox hook: {detail}",
    )


_GO_COMPILE_ERR = re.compile(r"^[^/\s].*\.go:\d+:\d+: .")


def _first_go_compile_error(stderr: str) -> str:
    """The first repo-relative `file:line:col: msg` go compiler error, skipping
    `-: # pkg` headers and the absolute-path duplicates erclint also prints."""
    for line in stderr.splitlines():
        line = line.rstrip()
        if _GO_COMPILE_ERR.match(line):
            return line
    return "unknown"


def _hook_compile_break(results: list) -> list[str]:
    """One `package <p> does not compile; first error: <...>` line per package
    erclint could not build (pkg / pkg.test variants deduped); empty when the
    package built."""
    erc = next((r for r in results if r.engine_id == "erclint"), None)
    if erc is None:
        return []
    pkgs = erclint_compile_broken_pkgs(erc.stdout)
    if not pkgs:
        return []
    first = _first_go_compile_error(erc.stderr)
    return [f"package {p} does not compile; first error: {first}" for p in pkgs]


# -- shared path helpers --------------------------------------------------


def _hook_rel_strict(target: Path, root: Path) -> str | None:
    """Return a repo-relative POSIX path, or None only for a real outside path."""
    try:
        resolved_target = target.resolve()
        resolved_root = root.resolve()
    except OSError as e:
        raise HookFileError(f"cannot resolve hook target {target}: {e}") from e
    if not resolved_target.is_relative_to(resolved_root):
        return None
    return resolved_target.relative_to(resolved_root).as_posix()


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError as e:
        raise HookFileError(f"cannot resolve hook path {a}: {e}") from e


def _hook_rel(target: Path, root: Path) -> str:
    try:
        resolved_target = target.resolve()
        resolved_root = root.resolve()
    except OSError as e:
        raise HookFileError(f"cannot resolve hook target {target}: {e}") from e
    if not resolved_target.is_relative_to(resolved_root):
        return str(target)
    return resolved_target.relative_to(resolved_root).as_posix()


if __name__ == "__main__":
    sys.exit(main())

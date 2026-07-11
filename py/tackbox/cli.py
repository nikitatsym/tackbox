"""tackbox lint / doctor CLI entry point."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from . import __version__, cache, doctor, reporters
from .engines import (
    EngineResult,
    EnginesStoreError,
    EngineSpec,
    active_engines,
    dispatch,
    engines_hash_hermetic,
    ensure_engines,
    erclint_compile_broken_pkgs,
    is_hermetic,
    located_findings,
    parse_erclint_findings,
    resolve_dev_versions,
    resolve_hermetic_versions,
    run_engines,
)
from .gitfiles import collect_source_set
from .source_set import (
    PathspecMagicError,
    group_go_packages_by_module,
    parse_git_diff_names,
    parse_ls_files_untracked,
)


class ChangedScopeError(ValueError):
    """Raised when the git commands backing --changed / --since fail."""

_BANNER_ORDER = ("erclint", "opengrep", "node", "eslint", "markdownlint")


def main(argv: list[str] | None = None) -> int:
    try:
        return _dispatch(argv)
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
            )
        except (
            PathspecMagicError,
            ChangedScopeError,
            cache.GoListError,
            reporters.ReportersError,
            EnginesStoreError,
        ) as e:
            # no-report: CLI boundary: surface as message + exit 2; a traceback here is the bug
            print(f"tackbox: {e}", file=sys.stderr)
            return 2
    if args.command == "doctor":
        _print_banner(_tackbox_root())
        return doctor.run(sys.stdout)
    if args.command == "hook":
        return _run_hook()
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
    sub.add_parser("doctor", help="verify the hermetic install is functional")
    sub.add_parser(
        "hook", help="Claude Code hook: PostToolUse lint + PreToolUse marker gate"
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
    machine: bool = False,
):
    """Run the lint pipeline for `scope`; return (results, warnings, orphans).

    results is None when the scope matched no files (nothing to lint), []
    when files matched but no engine applies, else the EngineResult list.
    Prints nothing - callers own the banner / warning / findings output.
    Infra failures (PathspecMagicError, GoListError, ReportersError, git)
    propagate to the caller.
    """
    reporter_pairs = reporters.pairs(reporters.load(repo_root))
    files, warnings = collect_source_set(repo_root, scope, changed_scope)
    if not files:
        return None, warnings, []

    plan = dispatch(files, active_engines())
    plan, go_orphans = _drop_go_orphans(plan, repo_root)
    if not plan:
        return [], warnings, go_orphans

    # Materialize the engine store once before the parallel run so worker
    # threads find it in place (dev mode has no store).
    if is_hermetic():
        ensure_engines()

    # Self-lint: tackbox lints itself. Cache is disabled so tackbox never
    # self-caches its own bugs (plan: "чтобы tackbox не самокэшировал").
    if tackbox_root.resolve() == repo_root.resolve():
        no_cache = True

    if no_cache:
        results = run_engines(plan, repo_root, tackbox_root, reporter_pairs, machine)
    else:
        cache_root = cache.default_cache_root()
        engines_hash = engines_hash_hermetic() if is_hermetic() else cache.engines_hash_dev(tackbox_root)
        cache.gc_stale_engines(engines_hash, cache_root)

        filtered_plan, pending = _apply_cache(plan, repo_root, engines_hash, cache_root)
        results = run_engines(filtered_plan, repo_root, tackbox_root, reporter_pairs, machine)
        _mark_clean_units(results, pending, engines_hash, cache_root)
        cache.gc_soft_cap(engines_hash, cache.SOFT_CAP, cache_root)
    return results, warnings, go_orphans


def _run_lint(scope: str, no_cache: bool, changed: bool, since: str | None) -> int:
    repo_root = _find_repo_root()
    tackbox_root = _tackbox_root()

    changed_scope: set[str] | None = None
    if changed or since is not None:
        changed_scope = _compute_changed_scope(repo_root, since)

    results, warnings, go_orphans = _lint_results(
        repo_root, tackbox_root, scope, no_cache, changed_scope
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
    if not results:
        return 0

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
    return _aggregate_exit(results)


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
        dirty_ips = {f.get("pkg") for f in findings}
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
    completed = subprocess.run(
        ["git", "diff", "--name-only", "-z", "HEAD"],
        cwd=repo_root,
        capture_output=True,
    )
    if completed.returncode != 0:
        # Fresh repo without any commits: HEAD does not resolve. Fail with a
        # clean tackbox message instead of a Python traceback on onboarding.
        err = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ChangedScopeError(
            f"--changed / --since requires at least one commit ({err})"
        )
    scope.update(parse_git_diff_names(completed.stdout))
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    ).stdout
    scope.update(parse_ls_files_untracked(untracked))
    if since is not None:
        completed = subprocess.run(
            ["git", "diff", "--name-only", "-z", f"{since}...HEAD"],
            cwd=repo_root,
            capture_output=True,
        )
        if completed.returncode != 0:
            err = completed.stderr.decode("utf-8", errors="replace").strip()
            raise ChangedScopeError(f"--since={since}: {err or 'git diff failed'}")
        scope.update(parse_git_diff_names(completed.stdout))
    return scope


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


# -- Claude Code hook -----------------------------------------------------

_HOOK_TOOLS = frozenset({"Edit", "Write", "MultiEdit"})
_MARKER_RE = re.compile(r"(?:no-report|parse-skip|nil-return|long-comment|test-skip|dup-ok):")


def _run_hook() -> int:
    """Dispatch a Claude Code hook event read as JSON from stdin.

    Unknown / missing event -> exit 0 (forward-compat: never break another
    hook consumer). Unreadable stdin / bad JSON -> exit 1 + one stderr line
    (non-blocking). No version banner in hook mode.
    """
    try:
        event = json.loads(sys.stdin.read())
        if not isinstance(event, dict):
            raise ValueError("hook event is not a JSON object")
    except (json.JSONDecodeError, ValueError, OSError) as e:
        # no-report: hook contract: bad stdin -> exit 1 + one stderr line, non-blocking
        print(f"tackbox hook: unreadable stdin: {e}", file=sys.stderr)
        return 1
    name = event.get("hook_event_name")
    if name == "PreToolUse":
        return _hook_pre(event)
    if name == "PostToolUse":
        return _hook_post(event)
    return 0


def _hook_repo_root(event: dict) -> Path | None:
    """Repo root for the event's cwd, or None if the guard fails.

    Guard (both modes): cwd must sit inside a git repo whose root holds a
    `dev.py`. Anywhere else the hook is a deliberate no-op.
    """
    cwd = event.get("cwd")
    if not cwd:
        return None
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        # no-report: git rev-parse cannot run here - not a git repo, the hook is a deliberate no-op
        return None
    if r.returncode != 0:
        return None
    root = Path(r.stdout.strip())
    if not (root / "dev.py").is_file():
        return None
    return root


def _hook_target(event: dict) -> tuple[Path | None, dict]:
    """(file_path, tool_input) for an Edit/Write/MultiEdit event, else
    (None, _) so the caller no-ops on other tools or a missing path."""
    if event.get("tool_name") not in _HOOK_TOOLS:
        return None, {}
    tool_input = event.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return None, tool_input
    return Path(file_path), tool_input


def _hook_post(event: dict) -> int:
    root = _hook_repo_root(event)
    if root is None:
        return 0
    target, tool_input = _hook_target(event)
    if target is None:
        return 0
    try:
        rel = str(target.resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        # no-report: edited file resolves outside the repo - nothing in scope to lint
        return 0

    try:
        results, _warnings, _orphans = _lint_results(
            root, _tackbox_root(), rel, no_cache=False, changed_scope=None, machine=True
        )
        if results is None:
            return 0  # scope matched no files
        # A non-compiling Go package must block with a readable line, not a raw
        # analyzer-load dump; checked before _located, which would raise on it.
        break_lines = _hook_compile_break(results)
        if break_lines:
            for line in break_lines:
                sys.stderr.write(line + "\n")
            return 2
        findings = _located(results, root)
    except (
        PathspecMagicError,
        ChangedScopeError,
        cache.GoListError,
        reporters.ReportersError,
        EnginesStoreError,
        subprocess.CalledProcessError,
        ValueError,  # a non-compile erclint analyzer-load error
    ) as e:
        # no-report: hook contract: infra error -> exit 1 + stderr, non-blocking
        print(f"tackbox hook: {e}", file=sys.stderr)
        return 1

    if not findings:
        return _hook_infra_or_clean(results)

    on_diff, elsewhere = _partition_findings(
        findings, rel, _affected_lines(event["tool_name"], tool_input, target)
    )
    if not on_diff:
        return 0  # nothing on the edited lines; dev.py check owns the whole file
    for f in on_diff:
        loc = f"{f.file}:{f.line}" if f.line is not None else (f.file or "?")
        sys.stderr.write(f"{loc}: {f.rule}\n")
    if elsewhere:
        sys.stderr.write(
            f"{len(elsewhere)} pre-existing elsewhere (dev.py check enforces)\n"
        )
    return 2


def _located(results: list, root: Path) -> list:
    return [f for r in results for f in located_findings(r.engine_id, r.stdout, root)]


def _hook_infra_or_clean(results: list) -> int:
    """No parseable findings: a nonzero engine exit is an infra failure (exit 1
    plus its stderr); otherwise the scope is clean."""
    if _aggregate_exit(results) == 0:
        return 0
    for r in results:
        if r.exit_code != 0 and r.stderr.strip():
            sys.stderr.write(r.stderr if r.stderr.endswith("\n") else r.stderr + "\n")
    return 1


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


def _affected_lines(tool_name: str, tool_input: dict, target: Path) -> set[int] | None:
    """Line numbers the edit touched in the post-edit file, or None for the whole
    file (Write leaves no pre-edit content to diff against).

    Edit spans its new_string; MultiEdit unions every edit's new_string. Every
    occurrence counts, so a coincidental repeat over-reports, never under-.
    """
    if tool_name == "Write":
        return None
    news = (
        [e.get("new_string") or "" for e in tool_input.get("edits") or []]
        if tool_name == "MultiEdit"
        else [tool_input.get("new_string") or ""]
    )
    if not any(news):
        return None  # no usable new_string -> whole file (over-report, never under)
    return _span_lines(target.read_text(), news)


def _span_lines(content: str, substrings: list[str]) -> set[int]:
    lines: set[int] = set()
    for sub in substrings:
        if not sub:
            continue
        start = 0
        while (idx := content.find(sub, start)) >= 0:
            first = content.count("\n", 0, idx) + 1
            lines.update(range(first, first + sub.count("\n") + 1))
            start = idx + 1
    return lines


def _partition_findings(
    findings: list, rel: str, affected: set[int] | None
) -> tuple[list, list]:
    """(on the edited diff, pre-existing elsewhere). An unknown location (file or
    line None) over-reports as on-diff rather than being dropped."""
    on_diff: list = []
    elsewhere: list = []
    for f in findings:
        if f.file is None:
            on_diff.append(f)
        elif f.file != rel:
            elsewhere.append(f)
        elif affected is None or f.line is None or f.line in affected:
            on_diff.append(f)
        else:
            elsewhere.append(f)
    return on_diff, elsewhere


def _hook_pre(event: dict) -> int:
    root = _hook_repo_root(event)
    if root is None:
        return 0
    target, tool_input = _hook_target(event)
    if target is None:
        return 0
    old, new, replace_all = _hook_pre_content(event["tool_name"], tool_input, target)
    rel = _hook_rel(target, root)

    if _same_path(target, root / reporters.FILENAME):
        added = _reporters_added_line(old, new)
        if added is not None:
            return _hook_ask(f".tackbox-reporters line added: {added}", rel)
        return 0

    marker = _marker_gate(old, new, replace_all)
    if marker is not None:
        return _hook_ask(f"suppression marker: {marker}", rel)
    return 0


def _hook_pre_content(
    tool_name: str, tool_input: dict, target: Path
) -> tuple[str, str, bool]:
    """(old, new, replace_all) content for the marker / line diff.

    Write compares against the file on disk (absent -> empty); Edit uses its
    old/new strings; MultiEdit concatenates every edit's strings.
    """
    if tool_name == "Write":
        old = target.read_text(encoding="utf-8") if target.is_file() else ""
        return old, tool_input.get("content") or "", False
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        old = "\n".join(e.get("old_string") or "" for e in edits)
        new = "\n".join(e.get("new_string") or "" for e in edits)
        return old, new, any(e.get("replace_all") for e in edits)
    return (
        tool_input.get("old_string") or "",
        tool_input.get("new_string") or "",
        bool(tool_input.get("replace_all")),
    )


def _markers(content: str) -> list[str]:
    """Each marker occurrence as `keyword: reason` (through end of its line,
    trimmed). The multiset drives new-marker and reason-change detection."""
    out: list[str] = []
    for m in _MARKER_RE.finditer(content):
        end = content.find("\n", m.start())
        out.append(content[m.start() : len(content) if end < 0 else end].strip())
    return out


def _marker_gate(old: str, new: str, replace_all: bool) -> str | None:
    """The marker string that must be approved, or None to allow.

    replace_all is conservative: any marker present in new but not old asks.
    Otherwise count-first: more markers -> ask; equal count but a changed
    marker/reason -> ask; fewer -> allow (removing a marker is free).
    """
    old_c, new_c = Counter(_markers(old)), Counter(_markers(new))
    added = list((new_c - old_c).elements())
    if replace_all:
        return added[0] if added else None
    if sum(new_c.values()) > sum(old_c.values()):
        return added[0] if added else next(iter(new_c))
    if sum(new_c.values()) == sum(old_c.values()) and new_c != old_c:
        return added[0] if added else "reason changed"
    return None


def _reporters_added_line(old: str, new: str) -> str | None:
    """A `.tackbox-reporters` line in new but not old (trim-normalized), or
    None when the change only removes lines."""
    old_c = Counter(s.strip() for s in old.splitlines() if s.strip())
    new_c = Counter(s.strip() for s in new.splitlines() if s.strip())
    added = list((new_c - old_c).elements())
    return added[0] if added else None


def _hook_ask(reason: str, rel: str) -> int:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": f"{reason} ({rel})",
                }
            }
        )
    )
    return 0


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        # no-report: unresolvable path is simply not the reporters file - guard
        return False


def _hook_rel(target: Path, root: Path) -> str:
    try:
        return str(target.resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        # no-report: unresolvable path - fall back to the raw target for the message
        return str(target)


if __name__ == "__main__":
    sys.exit(main())

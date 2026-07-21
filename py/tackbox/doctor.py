"""`tackbox doctor`: runtime verification of a hermetic install.

Contract: exit 0 all-ok, 1 any-fail, 2 usage. All checks always run
(no short-circuit). One `ok/fail <id>: <detail>` line per check on
stdout, ending with `doctor: N checks, M failed`. Between the check
lines and that summary sits the informational `attributes` section
(local divergence conditions) - never a check, no exit-code effect, and
absent when nothing holds.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from . import engines as engines_mod
from . import scopes
from .cache import sha256_tree
from .gitfiles import collect_snapshot
from .hashing import sha256_file
from .source_set import EXCLUSION_ATTRIBUTES, Snapshot


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    ok: bool
    detail: str


def run(out: TextIO) -> int:
    checks = _run_all_checks()
    for c in checks:
        status = "ok" if c.ok else "fail"
        out.write(f"{status} {c.check_id}: {c.detail}\n")
    _attributes_section(out)
    failed = sum(1 for c in checks if not c.ok)
    out.write(f"doctor: {len(checks)} checks, {failed} failed\n")
    return 0 if failed == 0 else 1


def _run_all_checks() -> list[CheckResult]:
    # One shared source snapshot feeds both toolchain sections: attributes
    # resolve once per doctor run, not once per section.
    snapshot = _resolve_source_snapshot()
    return [
        _check_platform(),
        _check_engines_store(),
        _check_payload_checksums(),
        _check_binaries_start(),
        _check_git_in_path(),
        _check_go_toolchain(snapshot),
        _check_java_toolchain(snapshot),
        _check_ast_grep(),
    ]


def _repo_root_or_none() -> Path | None:
    """The repo root for the process cwd, or None when git is absent or the cwd is
    not a repo. Guarded (not a swallowing try/except) so callers carry no marker."""
    if shutil.which("git") is None:
        return None
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.strip())


def _resolve_source_snapshot() -> Snapshot | None:
    """The one source inventory the Go and Java sections share. git absent or
    not-a-repo degrades to None (the toolchain probes then read 'not needed'); a
    genuine attribute-resolution failure is NOT degraded here - it travels as
    AttributeResolutionError, loud, never swallowed into 'no such sources'."""
    root = _repo_root_or_none()
    if root is None:
        return None
    return collect_snapshot(root)


def _snapshot_has_ext(snapshot: Snapshot | None, ext: str) -> bool:
    """True iff the shared snapshot's INCLUDED files carry `ext`. An
    attribute-excluded file is not a lint target, so a tree whose only sources of
    a language are excluded needs no toolchain for it."""
    return snapshot is not None and any(f.endswith(ext) for f in snapshot.included)


# -- attributes section (informational, not a check) -----------------------


def _attributes_section(out: TextIO) -> None:
    """Report local conditions that can make a run diverge from a clean-CI run:
    an info/attributes or an untracked/ignored carrier mentioning the exclusion
    attributes, a tracked carrier whose index state hides content from diff, a
    neutralized attribute source override. Strictly informational - never a check,
    no exit-code or tally effect. Class order is pinned; paths are lexicographic
    within a class; the whole section is absent when no condition holds."""
    root = _repo_root_or_none()
    if root is None:
        return
    lines: list[str] = []
    lines += _info_attributes_lines(root)
    lines += _untracked_carrier_lines(root)
    lines += _carrier_index_state_lines(root)
    lines += _attr_source_override_lines(root)
    if not lines:
        return
    out.write("attributes: local divergence conditions (informational, not a check):\n")
    for line in lines:
        out.write(f"  {line}\n")


def _mentions_attrs(content: str | None) -> bool:
    return content is not None and any(a in content for a in EXCLUSION_ATTRIBUTES)


def _is_gitattributes(rel: str) -> bool:
    return rel == ".gitattributes" or rel.endswith("/.gitattributes")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _info_attributes_lines(root: Path) -> list[str]:
    """The effective info/attributes, located via `git rev-parse --git-path` (in a
    linked worktree it lives in the common git dir), when it mentions the
    exclusion attributes."""
    completed = subprocess.run(
        ["git", "rev-parse", "--git-path", "info/attributes"],
        cwd=root, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        return []
    shown = completed.stdout.strip()
    path = Path(shown) if os.path.isabs(shown) else root / shown
    if not path.is_file() or not _mentions_attrs(_read_text(path)):
        return []
    return [f"info/attributes mentions exclusion attributes: {shown}"]


def _untracked_carrier_lines(root: Path) -> list[str]:
    """Untracked or ignored `.gitattributes` carriers mentioning the attributes -
    present locally but never in a fresh CI clone."""
    carriers: set[str] = set()
    for extra in (["--exclude-standard"], ["--ignored", "--exclude-standard"]):
        raw = subprocess.run(
            ["git", "ls-files", "--others", *extra, "-z"],
            cwd=root, capture_output=True,
        ).stdout
        for chunk in raw.split(b"\0"):
            rel = chunk.decode("utf-8", "replace")
            if rel and _is_gitattributes(rel):
                carriers.add(rel)
    lines: list[str] = []
    for rel in sorted(carriers):
        path = root / rel
        if path.is_file() and _mentions_attrs(_read_text(path)):
            lines.append(f"untracked .gitattributes mentions exclusion attributes: {rel}")
    return lines


def _carrier_index_state_lines(root: Path) -> list[str]:
    """Tracked `.gitattributes` carriers whose index state hides their content
    from an ordinary diff: skip-worktree / assume-unchanged bits, unmerged, or
    missing from the worktree. `git ls-files -v -z` tags each entry."""
    raw = subprocess.run(
        ["git", "ls-files", "-v", "-z"], cwd=root, capture_output=True
    ).stdout
    lines: list[str] = []
    states: list[tuple[str, str]] = []
    for chunk in raw.split(b"\0"):
        text = chunk.decode("utf-8", "replace")
        tag, sep, rel = text.partition(" ")
        if not sep or not _is_gitattributes(rel):
            continue
        state = _index_state(tag, root, rel)
        if state is not None and _mentions_attrs(_carrier_content(root, rel)):
            states.append((rel, state))
    for rel, state in sorted(states):
        lines.append(f"carrier index state hides content from diff: {rel} ({state})")
    return lines


def _index_state(tag: str, root: Path, rel: str) -> str | None:
    if not (root / rel).is_file():
        return "missing"
    if tag == "S":
        return "skip-worktree"
    if tag == "M":
        return "unmerged"
    if tag.islower():
        return "assume-unchanged"
    return None


def _carrier_content(root: Path, rel: str) -> str | None:
    """The carrier's content: the worktree copy when present, else the staged blob
    (a missing carrier still has its index content to inspect)."""
    path = root / rel
    if path.is_file():
        return _read_text(path)
    completed = subprocess.run(
        ["git", "show", f":{rel}"], cwd=root, capture_output=True, text=True
    )
    return completed.stdout if completed.returncode == 0 else None


def _attr_source_override_lines(root: Path) -> list[str]:
    """A neutralized attribute source override (`attr.tree` config or a set
    GIT_ATTR_SOURCE). The trailing `-c attr.tree=` neutralizes it for tackbox runs
    (verified on git 2.50.1); this line reports that it is present, not a failure.
    (A git version where the neutralization does not hold would instead warrant the
    conditional `attribute source override detected (unsupported; ...)` line - not
    emitted here, as the test matrix proves the neutralization holds.)"""
    overrides: list[str] = []
    completed = subprocess.run(
        ["git", "config", "--get", "attr.tree"], cwd=root, capture_output=True, text=True
    )
    if completed.returncode == 0 and completed.stdout.strip():
        overrides.append(f"attr.tree={completed.stdout.strip()}")
    env_source = os.environ.get("GIT_ATTR_SOURCE")
    if env_source:
        overrides.append(f"GIT_ATTR_SOURCE={env_source}")
    return [
        f"neutralized attribute source override: {o}" for o in sorted(overrides)
    ]


def _check_platform() -> CheckResult:
    resolved = engines_mod.detect_platform_key()
    if resolved is None:
        return CheckResult(
            "platform", False,
            f"unsupported: {sys.platform}/{platform.machine().lower()}",
        )
    if not engines_mod.is_hermetic():
        return CheckResult(
            "platform", True, f"{resolved} (dev mode; hermetic checks skipped)"
        )
    data = engines_mod.load_engines_json()
    baked = data.get("platform")
    if baked and baked != resolved:
        return CheckResult(
            "platform", False,
            f"wheel built for {baked}, running on {resolved}",
        )
    return CheckResult("platform", True, resolved)


def _check_engines_store() -> CheckResult:
    """Presence + whole-tree checksum of the engine store. Triggers the
    fetch-on-absence so a fresh install (and the fresh-runner canary) becomes
    self-healing rather than failing on a cold store."""
    if not engines_mod.is_hermetic():
        return CheckResult("engines-store", True, "skipped (dev mode)")
    try:
        root = engines_mod.ensure_engines()
    except engines_mod.EnginesStoreError as e:
        # no-report: doctor reports the store error via CheckResult (no short-circuit)
        return CheckResult("engines-store", False, str(e))
    if not root.is_dir():
        return CheckResult("engines-store", False, f"store missing: {root}")
    want = engines_mod.load_engines_json().get("store_sha256")
    if want and sha256_tree(root) != want:
        return CheckResult("engines-store", False, f"payload tree mismatch at {root}")
    return CheckResult("engines-store", True, str(root))


def _check_payload_checksums() -> CheckResult:
    if not engines_mod.is_hermetic():
        return CheckResult(
            "payload-checksums", True, "skipped (dev mode)"
        )
    data = engines_mod.load_engines_json()
    entries = data.get("engines", [])
    mismatches: list[str] = []
    missing: list[str] = []
    for entry in entries:
        rel_path = entry.get("path")
        expected = entry.get("sha256")
        if not rel_path or not expected:
            continue
        top, _, tail = rel_path.partition("/")
        if not tail:
            continue
        if top == "tackbox":
            root = engines_mod._TACKBOX_PKG_ROOT
        elif top == "tackbox_engines":
            root = engines_mod.hermetic_engines_root()
        else:
            continue
        target = root / tail
        if target.is_dir():
            # Directory entries (vendored npm packages, vendor-tree) carry a
            # tree digest; a file digest here would silently verify nothing.
            actual = sha256_tree(target)
        elif target.is_file():
            actual = sha256_file(target)
        else:
            missing.append(rel_path)
            continue
        if actual != expected:
            mismatches.append(rel_path)
    if missing or mismatches:
        parts = []
        if missing:
            parts.append(f"missing={len(missing)}")
        if mismatches:
            parts.append(f"mismatch={len(mismatches)}")
        return CheckResult(
            "payload-checksums", False, ", ".join(parts)
        )
    return CheckResult(
        "payload-checksums", True, f"{len(entries)} entries verified"
    )


def _check_binaries_start() -> CheckResult:
    if not engines_mod.is_hermetic():
        return CheckResult(
            "binaries-start", True, "skipped (dev mode)"
        )
    engines_root = engines_mod.hermetic_engines_root()
    tackbox_root = engines_mod._TACKBOX_PKG_ROOT
    exe = engines_mod.exe_name
    probes: list[tuple[str, list[str], bool]] = [
        ("erclint", [str(tackbox_root / "bin" / exe("erclint")), "--version"], True),
        (
            "erclint-opengrep",
            [str(tackbox_root / "bin" / exe("erclint-opengrep")), "--version"],
            True,
        ),
        ("opengrep", [str(engines_root / "bin" / exe("opengrep")), "--version"], True),
        ("node", [str(engines_root / "bin" / exe("node")), "--version"], True),
        ("jscpd", [str(engines_root / "bin" / exe("jscpd")), "--version"], True),
    ]
    env = engines_mod.hermetic_env()
    failures: list[str] = []
    for name, argv, require_zero in probes:
        try:
            # 120s: the probe checks startability, not latency - the first
            # exec of a binary just unpacked into the store can sit behind
            # the Windows antivirus scan far past any interactive timeout.
            completed = subprocess.run(
                argv, capture_output=True, timeout=120, env=env
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as e:
            # no-report: doctor collects every failure to report them all (no short-circuit)
            failures.append(f"{name}({type(e).__name__})")
            continue
        rc = completed.returncode
        if rc < 0:
            failures.append(f"{name}(signal={-rc})")
            continue
        if require_zero and rc != 0:
            failures.append(f"{name}(exit={rc})")
    if failures:
        return CheckResult("binaries-start", False, ", ".join(failures))
    return CheckResult("binaries-start", True, f"{len(probes)} binaries respond")


def _check_git_in_path() -> CheckResult:
    found = shutil.which("git")
    if not found:
        return CheckResult("git-in-path", False, "git not found in PATH")
    return CheckResult("git-in-path", True, found)


_AST_GREP_VERSION = "0.44.1"


def _check_ast_grep() -> CheckResult:
    """The outline engine (D015): a pip runtime dependency, resolved exactly as
    the engine resolves it (interpreter dir first, then PATH). Gate presence AND
    the pinned version - a grammar bump can silently change resolved scope
    chains (residual A7)."""
    found = scopes.ast_grep_exe()
    if not found:
        return CheckResult("ast-grep", False, "ast-grep not found (interpreter dir or PATH)")
    try:
        completed = subprocess.run(
            [found, "--version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as e:
        # no-report: doctor reports the ast-grep probe failure via CheckResult (no short-circuit)
        return CheckResult("ast-grep", False, f"cannot run ast-grep: {e}")
    text = completed.stdout or completed.stderr
    m = re.search(r"(\d+\.\d+\.\d+)", text)
    version = m.group(1) if m else "?"
    if version != _AST_GREP_VERSION:
        return CheckResult(
            "ast-grep", False, f"version {version} != pinned {_AST_GREP_VERSION} ({found})"
        )
    return CheckResult("ast-grep", True, f"{found} ({version})")


def _check_go_toolchain(snapshot: Snapshot | None) -> CheckResult:
    needed = _snapshot_has_ext(snapshot, ".go")
    found = shutil.which("go")
    if not needed:
        return CheckResult(
            "go-toolchain", True,
            found or "not needed (no .go files in source set)",
        )
    if not found:
        return CheckResult(
            "go-toolchain", False,
            "source set has .go files but `go` not on PATH",
        )
    return CheckResult("go-toolchain", True, found)


_JAVA_MIN_MAJOR = 17


def _check_java_toolchain(snapshot: Snapshot | None) -> CheckResult:
    """javalint runs on the system `java` (like erclint on `go`); the jar targets
    Java 17. Gate presence AND version so a too-old JVM fails loudly here rather
    than as an opaque class-version error mid-lint."""
    needed = _snapshot_has_ext(snapshot, ".java")
    found = shutil.which("java")
    if not needed:
        return CheckResult(
            "java-toolchain", True,
            found or "not needed (no .java files in source set)",
        )
    if not found:
        return CheckResult(
            "java-toolchain", False,
            "source set has .java files but `java` not on PATH",
        )
    major = _java_major_version(found)
    if major is None:
        return CheckResult(
            "java-toolchain", False, f"cannot determine java version from {found}"
        )
    if major < _JAVA_MIN_MAJOR:
        return CheckResult(
            "java-toolchain", False,
            f"java {major} < {_JAVA_MIN_MAJOR} required for javalint ({found})",
        )
    return CheckResult("java-toolchain", True, f"{found} (java {major})")


def _java_major_version(java: str) -> int | None:
    """Major version from `java -version` (which prints to stderr, e.g.
    `openjdk version "21.0.11"` or legacy `java version "1.8.0_..."`), or None
    when it cannot be run or parsed."""
    try:
        completed = subprocess.run(
            [java, "-version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        # no-report: doctor reports the unparseable/absent java via CheckResult
        return None
    text = completed.stderr or completed.stdout
    m = re.search(r'version "(\d+)(?:\.(\d+))?', text)
    if not m:
        return None
    major = int(m.group(1))
    # Legacy scheme `1.N` (1.8 = Java 8); modern scheme is the feature number.
    if major == 1 and m.group(2):
        return int(m.group(2))
    return major

"""`tackbox doctor`: runtime verification of a hermetic install.

Contract: exit 0 all-ok, 1 any-fail, 2 usage. All checks always run
(no short-circuit). One `ok/fail <id>: <detail>` line per check on
stdout, ending with `doctor: N checks, M failed`.
"""

from __future__ import annotations

import hashlib
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from . import engines as engines_mod
from .cache import sha256_tree
from .source_set import (
    filter_source_set,
    parse_ls_files_stage,
    parse_ls_files_untracked,
)


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
    failed = sum(1 for c in checks if not c.ok)
    out.write(f"doctor: {len(checks)} checks, {failed} failed\n")
    return 0 if failed == 0 else 1


def _run_all_checks() -> list[CheckResult]:
    return [
        _check_platform(),
        _check_engines_store(),
        _check_payload_checksums(),
        _check_binaries_start(),
        _check_git_in_path(),
        _check_go_toolchain(),
        _check_java_toolchain(),
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
            actual = _sha256_file(target)
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


def _check_go_toolchain() -> CheckResult:
    needed = _source_set_has_ext(".go")
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


def _check_java_toolchain() -> CheckResult:
    """javalint runs on the system `java` (like erclint on `go`); the jar targets
    Java 17. Gate presence AND version so a too-old JVM fails loudly here rather
    than as an opaque class-version error mid-lint."""
    needed = _source_set_has_ext(".java")
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


def _source_set_has_ext(ext: str) -> bool:
    try:
        repo_root = Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, check=True, text=True,
            ).stdout.strip()
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        # no-report: git absent or failed - treat as no such sources; the toolchain probe surfaces it
        return False
    try:
        stage_raw = subprocess.run(
            ["git", "ls-files", "-s", "-z"],
            cwd=repo_root, capture_output=True, check=True,
        ).stdout
        untracked_raw = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=repo_root, capture_output=True, check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        # no-report: git ls-files failed - doctor degrades to "no such sources", not a run failure
        return False
    files, _ = filter_source_set(
        parse_ls_files_stage(stage_raw),
        parse_ls_files_untracked(untracked_raw),
        ".",
        exists=lambda p: (repo_root / p).exists(),
        is_symlink=lambda p: (repo_root / p).is_symlink(),
    )
    return any(f.endswith(ext) for f in files)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

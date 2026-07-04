"""`tackbox doctor`: runtime verification of a hermetic install.

Contract: exit 0 all-ok, 1 any-fail, 2 usage. All checks always run
(no short-circuit). One `ok/fail <id>: <detail>` line per check on
stdout, ending with `doctor: N checks, M failed`.
"""

from __future__ import annotations

import hashlib
import platform
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

_SUPPORTED_PLATFORMS = {
    ("linux", "x86_64"): "linux-x86_64",
    ("linux", "amd64"): "linux-x86_64",
    ("linux", "aarch64"): "linux-aarch64",
    ("linux", "arm64"): "linux-aarch64",
    ("darwin", "arm64"): "macos-aarch64",
    ("darwin", "aarch64"): "macos-aarch64",
    ("windows", "x86_64"): "windows-x86_64",
    ("windows", "amd64"): "windows-x86_64",
}


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
        _check_payload_checksums(),
        _check_binaries_start(),
        _check_git_in_path(),
        _check_go_toolchain(),
    ]


def _check_platform() -> CheckResult:
    system = sys.platform
    if system.startswith("linux"):
        system = "linux"
    elif system.startswith("win") or system == "cygwin":
        system = "windows"
    else:
        system = "darwin" if system == "darwin" else system
    machine = platform.machine().lower()
    resolved = _SUPPORTED_PLATFORMS.get((system, machine))
    if resolved is None:
        return CheckResult(
            "platform", False, f"unsupported: {system}/{machine}"
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
    ]
    env = engines_mod.hermetic_env()
    failures: list[str] = []
    for name, argv, require_zero in probes:
        try:
            completed = subprocess.run(
                argv, capture_output=True, timeout=15, env=env
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as e:
            # no-sentry: doctor collects every failure to report them all (no short-circuit)
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
    needed = _source_set_has_go()
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


def _source_set_has_go() -> bool:
    try:
        repo_root = Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, check=True, text=True,
            ).stdout.strip()
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
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
        return False
    files, _ = filter_source_set(
        parse_ls_files_stage(stage_raw),
        parse_ls_files_untracked(untracked_raw),
        ".",
        exists=lambda p: (repo_root / p).exists(),
        is_symlink=lambda p: (repo_root / p).is_symlink(),
    )
    return any(f.endswith(".go") for f in files)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

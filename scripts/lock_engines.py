"""Verify or regenerate engines/lock.json.

The lock pins the two payload versions - engines/VERSION (tackbox-engines)
and engines/opengrep/VERSION (tackbox-opengrep) - together with sha256 of
the two files that describe the payloads:

- engines/manifest.json             (node/opengrep/jscpd versions + urls)
- engines/vendor/package-lock.json  (npm resolution)

Changing either without bumping the affected payload's VERSION would
silently change that payload under the same version thin pins - new
engines shipping under an unchanged pin. `--check` runs in the publish
workflow before any wheel touches PyPI; `--write` regenerates the lock
after a version bump or engine-source edit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO / "engines" / "VERSION"
OPENGREP_VERSION_FILE = REPO / "engines" / "opengrep" / "VERSION"
LOCK_FILE = REPO / "engines" / "lock.json"
MANIFEST_FILE = REPO / "engines" / "manifest.json"
VENDOR_LOCK_FILE = REPO / "engines" / "vendor" / "package-lock.json"

sys.path.insert(0, str(REPO / "py"))
from tackbox.hashing import sha256_file  # noqa: E402

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def read_version(path: Path) -> str:
    rel = path.relative_to(REPO).as_posix()
    if not path.is_file():
        raise SystemExit(f"missing {rel}")
    stripped = path.read_text().strip()
    if not SEMVER_RE.match(stripped):
        raise SystemExit(
            f"{rel} must be strict semver (major.minor.patch), got {stripped!r}"
        )
    return stripped


def compute_lock() -> dict:
    version = read_version(VERSION_FILE)
    opengrep_version = read_version(OPENGREP_VERSION_FILE)
    if not MANIFEST_FILE.is_file():
        raise SystemExit(f"missing {MANIFEST_FILE.relative_to(REPO)}")
    if not VENDOR_LOCK_FILE.is_file():
        raise SystemExit(f"missing {VENDOR_LOCK_FILE.relative_to(REPO)}")
    return {
        "schema": 1,
        "version": version,
        "opengrep_version": opengrep_version,
        "manifest_sha256": sha256_file(MANIFEST_FILE),
        "vendor_lock_sha256": sha256_file(VENDOR_LOCK_FILE),
    }


def render(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def cmd_write() -> int:
    lock = compute_lock()
    LOCK_FILE.write_text(render(lock))
    print(
        f"wrote {LOCK_FILE.relative_to(REPO)} for version {lock['version']}, "
        f"opengrep_version {lock['opengrep_version']}"
    )
    return 0


def cmd_check() -> int:
    expected = compute_lock()
    if not LOCK_FILE.is_file():
        print(
            f"engines/lock.json missing; run "
            f"`python scripts/lock_engines.py --write`",
            file=sys.stderr,
        )
        return 1
    try:
        current = json.loads(LOCK_FILE.read_text())
    except json.JSONDecodeError as e:
        # no-report: prints the parse error and fails the gate (exit 1)
        print(f"engines/lock.json is not valid JSON: {e}", file=sys.stderr)
        return 1
    fails: list[str] = []
    if current.get("schema") != 1:
        fails.append(f"schema: expected 1, got {current.get('schema')!r}")
    if current.get("version") != expected["version"]:
        fails.append(
            f"version drift: engines/VERSION is {expected['version']!r}, "
            f"lock.version is {current.get('version')!r}"
        )
    if current.get("opengrep_version") != expected["opengrep_version"]:
        fails.append(
            f"version drift: engines/opengrep/VERSION is {expected['opengrep_version']!r}, "
            f"lock.opengrep_version is {current.get('opengrep_version')!r}"
        )
    if current.get("manifest_sha256") != expected["manifest_sha256"]:
        fails.append(
            "manifest_sha256 drift: engines/manifest.json changed "
            "since last lock regen"
        )
    if current.get("vendor_lock_sha256") != expected["vendor_lock_sha256"]:
        fails.append(
            "vendor_lock_sha256 drift: engines/vendor/package-lock.json changed "
            "since last lock regen"
        )
    if fails:
        print("engines/lock.json is stale:", file=sys.stderr)
        for f in fails:
            print(f"  - {f}", file=sys.stderr)
        print(
            "regenerate with `python scripts/lock_engines.py --write`",
            file=sys.stderr,
        )
        return 1
    print(
        f"engines/lock.json ok (version {current['version']}, "
        f"opengrep_version {current['opengrep_version']})"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="verify lock matches sources")
    group.add_argument("--write", action="store_true", help="regenerate lock from sources")
    args = ap.parse_args()
    if args.check:
        return cmd_check()
    return cmd_write()


if __name__ == "__main__":
    sys.exit(main())

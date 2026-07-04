"""Verify or regenerate engines/lock.json.

The lock pins engines/VERSION together with sha256 of the two files that
describe the engine payload:

- engines/manifest.json      (node/opengrep versions + urls)
- engines/vendor/package-lock.json  (npm resolution)

Bumping any of these without also bumping engines/VERSION would silently
change the payload under the same fat wheel version pin in thin's METADATA
- new engines shipping under an unchanged Requires-Dist. `--check` runs
in the publish workflow before any wheel touches PyPI; `--write`
regenerates the lock after an engineer bumps engines/VERSION or edits an
engine source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO / "engines" / "VERSION"
LOCK_FILE = REPO / "engines" / "lock.json"
MANIFEST_FILE = REPO / "engines" / "manifest.json"
VENDOR_LOCK_FILE = REPO / "engines" / "vendor" / "package-lock.json"

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_version() -> str:
    if not VERSION_FILE.is_file():
        raise SystemExit(f"missing {VERSION_FILE.relative_to(REPO)}")
    raw = VERSION_FILE.read_text()
    stripped = raw.strip()
    if not SEMVER_RE.match(stripped):
        raise SystemExit(
            f"engines/VERSION must be strict semver (major.minor.patch), got {stripped!r}"
        )
    return stripped


def compute_lock() -> dict:
    version = read_version()
    if not MANIFEST_FILE.is_file():
        raise SystemExit(f"missing {MANIFEST_FILE.relative_to(REPO)}")
    if not VENDOR_LOCK_FILE.is_file():
        raise SystemExit(f"missing {VENDOR_LOCK_FILE.relative_to(REPO)}")
    return {
        "schema": 1,
        "version": version,
        "manifest_sha256": sha256_file(MANIFEST_FILE),
        "vendor_lock_sha256": sha256_file(VENDOR_LOCK_FILE),
    }


def render(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def cmd_write() -> int:
    lock = compute_lock()
    LOCK_FILE.write_text(render(lock))
    print(f"wrote {LOCK_FILE.relative_to(REPO)} for version {lock['version']}")
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
        # no-sentry: prints the parse error and fails the gate (exit 1)
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
    print(f"engines/lock.json ok (version {current['version']})")
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

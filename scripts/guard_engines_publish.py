"""Pre-publish integrity guard for the payload wheels (tackbox-engines,
tackbox-opengrep).

Each payload publish job uploads with skip-existing: true, so a re-run under
an unchanged payload version is a no-op on PyPI. That is safe only while the
payload is identical: thin pins store_sha256 (the payload tree-sha) and
install-time verification refuses a fetched wheel whose tree-sha differs.
Editing a payload input without bumping its VERSION leaves skip-existing
holding the OLD wheel on PyPI while the new thin ships pointing at the NEW
tree-sha - every fresh install then hits a permanent EnginesStoreError.

This guard runs before the publish action: for each local payload wheel it
finds the same-platform wheel already on PyPI (matched by exact filename, the
same key skip-existing uses) and compares payload tree-shas with
payload_tree_sha256 - the exact digest install-time verification uses.
Unpublished version/platform is a fresh upload; identical payload an
idempotent re-run; changed payload a hard fail. No CDN wait needed: it
checks the already-converged published version.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "py"))
from tackbox.engines import PAYLOADS, Payload, payload_tree_sha256  # noqa: E402
from tackbox.hashing import sha256_file  # noqa: E402

PYPI_PROJECT_JSON = "https://pypi.org/pypi/{project}/json"

# The repo file holding each payload's version, named in the bump hint.
VERSION_FILES = {"engines": "engines/VERSION", "opengrep": "engines/opengrep/VERSION"}

FRESH = "fresh"
MATCH = "match"
MISMATCH = "mismatch"

# GET returning (status, body). A 404 (package or version not yet on PyPI)
# comes back as a status, not an exception - ordinary control flow; transport
# failures (DNS, TLS, refused) still raise from .open().
HttpGet = Callable[[str], "tuple[int, bytes]"]
# (PyPI file entry, workdir) -> downloaded wheel path.
Downloader = Callable[[dict, Path], Path]


@dataclass(frozen=True)
class GuardResult:
    wheel: str
    status: str  # FRESH | MATCH | MISMATCH
    local_sha: str
    published_sha: str | None


def compare_wheel(payload: Payload, local_wheel: Path, published_wheel: Path | None) -> GuardResult:
    """Pure compare core (no network): classify one local payload wheel against
    the same-platform wheel already on PyPI, or None when that version/platform
    is not yet published. Both sides go through payload_tree_sha256."""
    local_sha = payload_tree_sha256(local_wheel, payload.package)
    if published_wheel is None:
        return GuardResult(local_wheel.name, FRESH, local_sha, None)
    published_sha = payload_tree_sha256(published_wheel, payload.package)
    status = MATCH if published_sha == local_sha else MISMATCH
    return GuardResult(local_wheel.name, status, local_sha, published_sha)


class _KeepErrorResponses(urllib.request.HTTPErrorProcessor):
    """Hand back 4xx/5xx as ordinary responses instead of raising, so a 404 is a
    readable status code. Transport-level failures still raise from .open()."""

    def http_response(self, request, response):
        return response

    https_response = http_response


_PYPI_OPENER = urllib.request.build_opener(_KeepErrorResponses)


def _default_http_get(url: str) -> tuple[int, bytes]:
    with _PYPI_OPENER.open(url, timeout=60) as resp:
        return resp.status, resp.read()


def _default_download(entry: dict, workdir: Path) -> Path:
    """Download one published wheel into workdir, verifying it against the
    index's own sha256 (truncation, corrupting proxy). Loud on any failure."""
    name = entry["filename"]
    dest = workdir / name
    with urlopen(entry["url"], timeout=300) as r, dest.open("wb") as out:
        shutil.copyfileobj(r, out)
    claimed = (entry.get("digests") or {}).get("sha256")
    if claimed:
        got = sha256_file(dest)
        if got != claimed:
            raise SystemExit(
                f"downloaded {name} does not match PyPI index digest: "
                f"index {claimed}, got {got}"
            )
    return dest


def published_release_files(
    payload: Payload, version: str, http_get: "HttpGet | None" = None
) -> list[dict]:
    """PyPI file entries for <payload project> <version>, or [] when the project
    or that version is not yet on PyPI. A 404 is the first-ever release (nothing
    to verify); any other non-200 is loud - a flaky PyPI must not read as
    'clear'."""
    http_get = http_get or _default_http_get
    url = PYPI_PROJECT_JSON.format(project=payload.project)
    status, body = http_get(url)
    if status == 404:
        return []
    if status != 200:
        raise SystemExit(f"cannot query {url}: HTTP {status}")
    meta = json.loads(body.decode("utf-8"))
    return meta.get("releases", {}).get(version, [])


def guard_wheels(
    payload: Payload,
    wheel_dir: Path,
    version: str,
    workdir: Path,
    http_get: "HttpGet | None" = None,
    download: "Downloader | None" = None,
) -> list[GuardResult]:
    """Compare every local payload wheel in wheel_dir against its same-platform
    already-published counterpart. Per-platform matching is by exact wheel
    filename (<package>-<version>-py3-none-<wheel_plat>.whl): the platform tag
    lives in the name, and it is the same key PyPI's skip-existing keys on, so
    the guard verifies exactly the wheel a skipped re-upload would leave."""
    download = download or _default_download
    pattern = f"{payload.package}-*.whl"
    local_wheels = sorted(wheel_dir.glob(pattern))
    if not local_wheels:
        raise SystemExit(f"no {pattern} found in {wheel_dir}")
    published = {
        f["filename"]: f for f in published_release_files(payload, version, http_get)
    }
    results: list[GuardResult] = []
    for wheel in local_wheels:
        entry = published.get(wheel.name)
        pub_wheel = download(entry, workdir) if entry is not None else None
        results.append(compare_wheel(payload, wheel, pub_wheel))
    return results


def _render(result: GuardResult) -> str:
    if result.status == FRESH:
        return f"{result.wheel}: fresh (version/platform not yet on PyPI) - ok"
    if result.status == MATCH:
        return f"{result.wheel}: payload matches published wheel (idempotent re-run) - ok"
    return (
        f"{result.wheel}: MISMATCH - published {result.published_sha}, "
        f"local {result.local_sha}"
    )


def main(argv: list[str] | None = None) -> int:
    payloads = {p.key: p for p in PAYLOADS}
    ap = argparse.ArgumentParser(description="pre-publish payload integrity guard")
    ap.add_argument("--payload", required=True, choices=sorted(payloads))
    ap.add_argument(
        "--wheel-dir", required=True,
        help="directory holding the local payload wheels to publish",
    )
    ap.add_argument(
        "--version", required=True,
        help="payload version to check on PyPI (contents of its VERSION file)",
    )
    args = ap.parse_args(argv)
    payload = payloads[args.payload]

    with tempfile.TemporaryDirectory() as td:
        results = guard_wheels(payload, Path(args.wheel_dir), args.version.strip(), Path(td))

    for result in results:
        print(_render(result))
    mismatches = [r for r in results if r.status == MISMATCH]
    if mismatches:
        names = ", ".join(r.wheel for r in mismatches)
        print(
            f"\n{payload.project} payload changed but {VERSION_FILES[payload.key]} "
            f"was not bumped ({names}) - bump it and regenerate engines/lock.json",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

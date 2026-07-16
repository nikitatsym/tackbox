"""Pre-publish integrity guard for the fat engines wheel.

publish-fat uploads with skip-existing: true, so a re-run under an unchanged
engines/VERSION is a no-op on PyPI. That is safe only while the payload is
identical: thin pins store_sha256 = the fat payload tree-sha, and install-time
verification refuses a fetched fat whose tree-sha differs. If an engineer edits
a fat input without bumping engines/VERSION, skip-existing keeps the OLD fat on
PyPI while the new thin ships pointing at the NEW tree-sha - every fresh install
then hits a permanent EnginesStoreError.

This guard runs before the publish action. For each local fat wheel about to be
published it finds the same-platform wheel already on PyPI (matched by exact
filename, the same key skip-existing uses) and compares payload tree-shas with
engines_payload_tree_sha256 - the exact digest install-time verification uses.
A version/platform not yet published is a fresh upload (nothing to verify); an
identical payload is a safe idempotent re-run; a changed payload is a hard fail.
Because it checks the already-converged published version, no CDN wait is needed.
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
from tackbox.engines import engines_payload_tree_sha256  # noqa: E402
from tackbox.hashing import sha256_file  # noqa: E402

PYPI_ENGINES_JSON = "https://pypi.org/pypi/tackbox-engines/json"

FRESH = "fresh"
MATCH = "match"
MISMATCH = "mismatch"

# GET returning (status, body). A 404 (package or version not yet on PyPI) comes
# back as a status, not an exception, so it is ordinary control flow; a genuine
# transport failure (DNS, TLS, refused) still raises loudly from .open().
HttpGet = Callable[[str], "tuple[int, bytes]"]
# (PyPI file entry, workdir) -> downloaded wheel path.
Downloader = Callable[[dict, Path], Path]


@dataclass(frozen=True)
class GuardResult:
    wheel: str
    status: str  # FRESH | MATCH | MISMATCH
    local_sha: str
    published_sha: str | None


def compare_fat_wheel(local_wheel: Path, published_wheel: Path | None) -> GuardResult:
    """Pure compare core (no network): classify one local fat wheel against the
    same-platform wheel already on PyPI, or None when that version/platform is
    not yet published. Both sides go through engines_payload_tree_sha256."""
    local_sha = engines_payload_tree_sha256(local_wheel)
    if published_wheel is None:
        return GuardResult(local_wheel.name, FRESH, local_sha, None)
    published_sha = engines_payload_tree_sha256(published_wheel)
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


def published_release_files(version: str, http_get: "HttpGet | None" = None) -> list[dict]:
    """PyPI file entries for tackbox-engines <version>, or [] when the package or
    that version is not yet on PyPI. A 404 is the first-ever release (nothing to
    verify); any other non-200 is loud - a flaky PyPI must not read as 'clear'."""
    http_get = http_get or _default_http_get
    status, body = http_get(PYPI_ENGINES_JSON)
    if status == 404:
        return []
    if status != 200:
        raise SystemExit(f"cannot query {PYPI_ENGINES_JSON}: HTTP {status}")
    meta = json.loads(body.decode("utf-8"))
    return meta.get("releases", {}).get(version, [])


def guard_wheels(
    fat_dir: Path,
    version: str,
    workdir: Path,
    http_get: "HttpGet | None" = None,
    download: "Downloader | None" = None,
) -> list[GuardResult]:
    """Compare every local fat wheel in fat_dir against its same-platform
    already-published counterpart. Per-platform matching is by exact wheel
    filename (tackbox_engines-<version>-py3-none-<wheel_plat>.whl): the platform
    tag lives in the name, and it is the same key PyPI's skip-existing keys on,
    so the guard verifies exactly the wheel a skipped re-upload would leave."""
    download = download or _default_download
    local_wheels = sorted(fat_dir.glob("tackbox_engines-*.whl"))
    if not local_wheels:
        raise SystemExit(f"no tackbox_engines-*.whl found in {fat_dir}")
    published = {
        f["filename"]: f for f in published_release_files(version, http_get)
    }
    results: list[GuardResult] = []
    for wheel in local_wheels:
        entry = published.get(wheel.name)
        pub_wheel = download(entry, workdir) if entry is not None else None
        results.append(compare_fat_wheel(wheel, pub_wheel))
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
    ap = argparse.ArgumentParser(description="pre-publish fat-payload integrity guard")
    ap.add_argument(
        "--fat-dir", required=True,
        help="directory holding the local tackbox_engines-*.whl to publish",
    )
    ap.add_argument(
        "--version", required=True,
        help="engines version to check on PyPI (contents of engines/VERSION)",
    )
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory() as td:
        results = guard_wheels(Path(args.fat_dir), args.version.strip(), Path(td))

    for result in results:
        print(_render(result))
    mismatches = [r for r in results if r.status == MISMATCH]
    if mismatches:
        names = ", ".join(r.wheel for r in mismatches)
        print(
            f"\nfat payload changed but engines/VERSION was not bumped ({names}) "
            f"- bump engines/VERSION and regenerate engines/lock.json",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

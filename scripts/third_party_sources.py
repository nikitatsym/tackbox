"""Keep verified release copies. Fetch upstream only when the asset is absent; never overwrite."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

REPO = Path(__file__).resolve().parent.parent
LICENSES = REPO / "engines" / "licenses"

RELEASE_TITLE = "Third-party sources"
RELEASE_NOTES = (
    "Corresponding source for third-party code in tackbox's wheels. Each file is an unchanged "
    "copy; the license inventory in the wheel names its original location and sha512."
)

Gh = Callable[[list[str]], "subprocess.CompletedProcess[str]"]
Download = Callable[[str, Path], None]


def source_copies(licenses: Path = LICENSES) -> list[dict[str, str]]:
    copies = []
    for inventory in sorted(licenses.glob("*/inventory.json")):
        for component in json.loads(inventory.read_text())["components"]:
            if "source_copy" in component:
                copies.append(component)
    return copies


def release_asset(url: str) -> tuple[str, str, str]:
    """(owner/repo, tag, asset) of a GitHub release download URL."""
    parts = urlparse(url).path.strip("/").split("/")
    if urlparse(url).netloc != "github.com" or len(parts) != 6 or parts[2:4] != ["releases", "download"]:
        raise SystemExit(f"not a GitHub release download URL: {url}")
    return f"{parts[0]}/{parts[1]}", parts[4], parts[5]


def sha512_file(path: Path) -> str:
    h = hashlib.sha512()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _gh(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *argv], capture_output=True, text=True)


def _download(url: str, dest: Path) -> None:
    try:
        with urlopen(url, timeout=300) as r, dest.open("wb") as out:
            shutil.copyfileobj(r, out)
    except (OSError, HTTPException) as error:
        raise SystemExit(f"{url}: {error}") from error


def _checked(result: subprocess.CompletedProcess[str]) -> subprocess.CompletedProcess[str]:
    if result.returncode != 0:
        raise SystemExit(f"gh {' '.join(result.args[1:])} exited {result.returncode}: {result.stderr.strip()}")
    return result


def _fetch_verified(url: str, dest: Path, sha512: str, download: Download) -> None:
    download(url, dest)
    got = sha512_file(dest)
    if got != sha512:
        raise SystemExit(f"{url}: sha512 {got}, inventory records {sha512}")


def keep(component: dict[str, str], workdir: Path, gh: Gh = _gh, download: Download = _download) -> str:
    repo, tag, asset = release_asset(component["source_copy"])
    view = gh(["release", "view", tag, "--repo", repo, "--json", "assets"])
    released = view.returncode == 0
    if not released and "release not found" not in view.stderr:
        _checked(view)
    present = released and asset in {a["name"] for a in json.loads(view.stdout)["assets"]}
    if present:
        status = "present"
    else:
        original = workdir / "original" / asset
        original.parent.mkdir(parents=True, exist_ok=True)
        _fetch_verified(component["source"], original, component["source_sha512"], download)
        if released:
            _checked(gh(["release", "upload", tag, str(original), "--repo", repo]))
        else:
            _checked(gh(["release", "create", tag, str(original), "--repo", repo, "--title", RELEASE_TITLE,
                         "--notes", RELEASE_NOTES, "--latest=false"]))
        status = f"uploaded from {component['source']}"
    public = workdir / "public" / asset
    public.parent.mkdir(parents=True, exist_ok=True)
    _fetch_verified(component["source_copy"], public, component["source_sha512"], download)
    return f"{asset}: {status}; verified {component['source_copy']} (sha512)"


def main() -> int:
    with tempfile.TemporaryDirectory() as workdir:
        for component in source_copies():
            print(keep(component, Path(workdir)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import hashlib
import http.client
import io
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import third_party_sources  # noqa: E402

_ORIGINAL_URL = "https://example.invalid/gmp-src.tar.zst"
_COPY_URL = "https://github.com/owner/repo/releases/download/third-party-sources/gmp-src.tar.zst"
_SOURCE = b"gmp source package\n"
_COMPONENT = {
    "id": "libgmp-10.dll", "source": _ORIGINAL_URL,
    "source_sha512": hashlib.sha512(_SOURCE).hexdigest(), "source_copy": _COPY_URL,
}


class _Gh:
    def __init__(self, release: bool, asset: bool):
        self.release, self.asset, self.calls = release, asset, []

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:2] == ["release", "view"]:
            if not self.release:
                return subprocess.CompletedProcess(["gh", *argv], 1, "", "release not found\n")
            assets = '[{"name": "gmp-src.tar.zst"}]' if self.asset else "[]"
            return subprocess.CompletedProcess(["gh", *argv], 0, f'{{"assets": {assets}}}', "")
        return subprocess.CompletedProcess(["gh", *argv], 0, "", "")

    def verbs(self):
        return [argv[1] for argv in self.calls]


class _Served:
    def __init__(self, **by_url: bytes):
        self.by_url, self.fetched = by_url, []

    def __call__(self, url, dest):
        self.fetched.append(url)
        if url not in self.by_url:
            raise SystemExit(f"{url}: HTTP Error 404: Not Found")
        dest.write_bytes(self.by_url[url])


def test_the_windows_gmp_source_package_is_kept_as_a_copy():
    [component] = third_party_sources.source_copies()
    assert component["id"] == "libgmp-10.dll"
    assert third_party_sources.release_asset(component["source_copy"]) == (
        "nikitatsym/tackbox", "third-party-sources", "mingw64-x86_64-gmp-6.3.0-1-src.tar.zst",
    )


def test_the_first_copy_is_attached_when_the_release_is_created(tmp_path):
    gh = _Gh(release=False, asset=False)
    download = _Served(**{_ORIGINAL_URL: _SOURCE, _COPY_URL: _SOURCE})
    assert third_party_sources.keep(_COMPONENT, tmp_path, gh, download) == (
        f"gmp-src.tar.zst: uploaded from {_ORIGINAL_URL}; verified {_COPY_URL} (sha512)"
    )
    assert gh.verbs() == ["view", "create"]
    assert gh.calls[1][3] == str(tmp_path / "original" / "gmp-src.tar.zst")
    assert download.fetched == [_ORIGINAL_URL, _COPY_URL]


def test_a_release_without_the_copy_gets_it_appended(tmp_path):
    gh = _Gh(release=True, asset=False)
    download = _Served(**{_ORIGINAL_URL: _SOURCE, _COPY_URL: _SOURCE})
    third_party_sources.keep(_COMPONENT, tmp_path, gh, download)
    assert gh.verbs() == ["view", "upload"]
    assert download.fetched == [_ORIGINAL_URL, _COPY_URL]


def test_a_present_copy_is_checked_at_its_public_url_only(tmp_path):
    gh = _Gh(release=True, asset=True)
    download = _Served(**{_COPY_URL: _SOURCE})
    assert third_party_sources.keep(_COMPONENT, tmp_path, gh, download) == (
        f"gmp-src.tar.zst: present; verified {_COPY_URL} (sha512)"
    )
    assert gh.verbs() == ["view"]
    assert download.fetched == [_COPY_URL]


@pytest.mark.parametrize("asset", [True, False], ids=["present", "just-uploaded"])
def test_a_public_copy_that_differs_fails_without_replacing_it(tmp_path, asset):
    gh = _Gh(release=True, asset=asset)
    download = _Served(**{_ORIGINAL_URL: _SOURCE, _COPY_URL: b"other bytes\n"})
    with pytest.raises(SystemExit, match=f"{_COPY_URL}: sha512"):
        third_party_sources.keep(_COMPONENT, tmp_path, gh, download)
    assert gh.verbs() == (["view"] if asset else ["view", "upload"])


def test_an_unservable_public_copy_fails_naming_its_url(tmp_path):
    gh = _Gh(release=True, asset=True)
    with pytest.raises(SystemExit, match=f"{_COPY_URL}: HTTP Error 404"):
        third_party_sources.keep(_COMPONENT, tmp_path, gh, _Served())


@pytest.mark.parametrize("release", [True, False], ids=["release", "no-release"])
def test_an_original_that_differs_creates_and_uploads_nothing(tmp_path, release):
    gh = _Gh(release=release, asset=False)
    download = _Served(**{_ORIGINAL_URL: b"changed\n"})
    with pytest.raises(SystemExit, match=f"{_ORIGINAL_URL}: sha512"):
        third_party_sources.keep(_COMPONENT, tmp_path, gh, download)
    assert gh.verbs() == ["view"]


def test_a_failing_release_lookup_creates_nothing(tmp_path):
    def gh(argv):
        return subprocess.CompletedProcess(["gh", *argv], 1, "", "HTTP 403: Resource not accessible\n")

    with pytest.raises(SystemExit, match="gh release view .* exited 1: HTTP 403"):
        third_party_sources.keep(_COMPONENT, tmp_path, gh, _Served())


def test_a_transport_failure_names_the_url(tmp_path):
    url = (tmp_path / "missing.tar.zst").as_uri()
    with pytest.raises(SystemExit, match=f"{url}: "):
        third_party_sources._download(url, tmp_path / "out")


def test_a_truncated_body_names_the_url(tmp_path, monkeypatch):
    class _Truncated(io.BytesIO):
        def read(self, size=-1):
            raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(third_party_sources, "urlopen", lambda url, timeout: _Truncated())
    with pytest.raises(SystemExit, match=f"{_COPY_URL}: IncompleteRead"):
        third_party_sources._download(_COPY_URL, tmp_path / "out")


def test_a_non_release_url_is_refused():
    with pytest.raises(SystemExit, match="not a GitHub release download URL"):
        third_party_sources.release_asset(_ORIGINAL_URL)

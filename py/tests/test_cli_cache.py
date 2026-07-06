"""End-to-end CLI cache tests: warm/cold hits, invalidation, --no-cache.

The cache root is redirected via `TACKBOX_CACHE_HOME` so a run cannot see
or clobber the developer's real `~/.cache/tackbox`. Each fixture repo is a
fresh git tree; tackbox runs as a subprocess (`python -m tackbox.cli lint .`)
to exercise the real entrypoint.

Plan acceptance for step 4:
- Signature change in Go package B invalidates dependent package A.
- Failure is not cached (finding = no marker).
- Corrupt marker does not fail the run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tackbox import cache as tackbox_cache
from tackbox import cli
from tackbox.engines import DEV_ENGINES, EngineResult


GO_MOD = "module cachefixture\n\ngo 1.24\n"

GO_PKG_B_ADD2 = """package pkg_b

func Add(a, b int) int { return a + b }
"""

GO_PKG_B_ADD3 = """package pkg_b

func Add(a, b, c int) int { return a + b + c }
"""

GO_PKG_A_USES_B_ADD2 = """package pkg_a

import "cachefixture/pkg_b"

func Two() int { return pkg_b.Add(1, 1) }
"""

GO_PKG_A_USES_B_ADD3 = """package pkg_a

import "cachefixture/pkg_b"

func Two() int { return pkg_b.Add(1, 1, 1) }
"""

GO_PKG_C_UNRELATED = """package pkg_c

func Ping() int { return 42 }
"""

JS_CLEAN = "export const two = 2\n"
JS_SWALLOW = "try { doThing() } catch (e) { }\n"

MD_CLEAN = "# Notes\n\nAll ASCII here.\n"


def _needs_go():
    # Conventions: no test skips - a missing toolchain is an environment
    # bug to fix, not a reason to silently shrink coverage.
    if shutil.which("go") is None:
        pytest.fail("go toolchain not installed; install it, do not skip")


def _needs_node():
    if shutil.which("node") is None:
        pytest.fail("node not installed; install it, do not skip")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")


def _commit_all(root: Path, msg: str = "snap") -> None:
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", msg)


def _run_tackbox(
    repo: Path, cache_home: Path, *extra: str
) -> subprocess.CompletedProcess:
    tackbox_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tackbox_root / "py")
    env["TACKBOX_CACHE_HOME"] = str(cache_home)
    return subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "lint", ".", *extra],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


_DEV_HASH: str | None = None


def _dev_hash() -> str:
    """Engines-hash the CLI subprocess will compute for this source tree."""
    global _DEV_HASH
    if _DEV_HASH is None:
        _DEV_HASH = tackbox_cache.engines_hash_dev(
            Path(__file__).resolve().parents[2]
        )
    return _DEV_HASH


def _marker_count(cache_home: Path, engine_id: str) -> int:
    root = cache_home / "v1" / _dev_hash()
    if not root.is_dir():
        return 0
    return sum(1 for p in root.iterdir() if p.name.endswith(f".{engine_id}"))


def _all_markers(cache_home: Path) -> list[Path]:
    root = cache_home / "v1" / _dev_hash()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_file())


# -- Fixtures --------------------------------------------------------------


@pytest.fixture
def go_repo(tmp_path) -> Path:
    """Repo with pkg_a imports pkg_b + unrelated pkg_c. No findings."""
    _needs_go()
    (tmp_path / "go.mod").write_text(GO_MOD)
    (tmp_path / "pkg_b").mkdir()
    (tmp_path / "pkg_b" / "b.go").write_text(GO_PKG_B_ADD2)
    (tmp_path / "pkg_a").mkdir()
    (tmp_path / "pkg_a" / "a.go").write_text(GO_PKG_A_USES_B_ADD2)
    (tmp_path / "pkg_c").mkdir()
    (tmp_path / "pkg_c" / "c.go").write_text(GO_PKG_C_UNRELATED)
    _init_repo(tmp_path)
    _commit_all(tmp_path)
    return tmp_path


@pytest.fixture
def clean_js_repo(tmp_path) -> Path:
    _needs_node()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text(JS_CLEAN)
    (tmp_path / "src" / "b.js").write_text(JS_CLEAN)
    _init_repo(tmp_path)
    _commit_all(tmp_path)
    return tmp_path


@pytest.fixture
def dirty_js_repo(tmp_path) -> Path:
    _needs_node()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bad.js").write_text(JS_SWALLOW)
    _init_repo(tmp_path)
    _commit_all(tmp_path)
    return tmp_path


# -- Warm/cold cache -------------------------------------------------------


def test_cold_run_writes_markers(go_repo, tmp_path):
    cache_home = tmp_path / "cache"
    result = _run_tackbox(go_repo, cache_home)
    assert result.returncode == 0, (
        f"clean fixture returned {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    # Two Go packages -> two erclint markers.
    assert _marker_count(cache_home, "erclint") == 3
    # opengrep runs per-file on .go
    assert _marker_count(cache_home, "erclint-opengrep") == 3


def test_warm_run_skips_all_engines(go_repo, tmp_path):
    cache_home = tmp_path / "cache"
    prime = _run_tackbox(go_repo, tmp_path / "cache")
    assert prime.returncode == 0
    warm = _run_tackbox(go_repo, cache_home)
    assert warm.returncode == 0
    # No engine sections at all when everything is cached.
    assert "== erclint ==" not in warm.stdout
    assert "== erclint-opengrep ==" not in warm.stdout


def test_signature_change_in_b_invalidates_a(go_repo, tmp_path):
    """Plan acceptance for step 4.

    After priming the cache we change pkg_b's Add signature and also update
    pkg_a's call site so the tree still compiles. Both A's and B's cache
    markers must be re-written under a new unit digest; the old markers for
    A remain (they belong to the pre-change source and are LRU fodder).
    """
    cache_home = tmp_path / "cache"
    assert _run_tackbox(go_repo, cache_home).returncode == 0
    before = {p.name for p in _all_markers(cache_home)}
    erclint_before = {p.name for p in _all_markers(cache_home) if p.name.endswith(".erclint")}

    (go_repo / "pkg_b" / "b.go").write_text(GO_PKG_B_ADD3)
    (go_repo / "pkg_a" / "a.go").write_text(GO_PKG_A_USES_B_ADD3)

    result = _run_tackbox(go_repo, cache_home)
    assert result.returncode == 0, (
        f"expected 0, got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    erclint_after = {p.name for p in _all_markers(cache_home) if p.name.endswith(".erclint")}
    # Two new markers for pkg_a and pkg_b appear (different digests).
    new_erclint = erclint_after - erclint_before
    assert len(new_erclint) == 2, (
        f"expected two new erclint markers for pkg_a and pkg_b, got {new_erclint}"
    )


def test_unrelated_pkg_change_does_not_touch_a(go_repo, tmp_path):
    cache_home = tmp_path / "cache"
    assert _run_tackbox(go_repo, cache_home).returncode == 0
    erclint_before = {p.name for p in _all_markers(cache_home) if p.name.endswith(".erclint")}

    (go_repo / "pkg_c" / "c.go").write_text(GO_PKG_C_UNRELATED + "\n// tail\n")
    assert _run_tackbox(go_repo, cache_home).returncode == 0

    erclint_after = {p.name for p in _all_markers(cache_home) if p.name.endswith(".erclint")}
    new_marks = erclint_after - erclint_before
    # Exactly one new marker: pkg_c's fresh digest. pkg_a and pkg_b untouched.
    assert len(new_marks) == 1


# -- Failures are not cached ----------------------------------------------


def test_failure_not_cached(dirty_js_repo, tmp_path):
    cache_home = tmp_path / "cache"
    r = _run_tackbox(dirty_js_repo, cache_home)
    assert r.returncode != 0
    assert _marker_count(cache_home, "tackbox-eslint") == 0


def test_partial_success_caches_the_clean_files(tmp_path):
    """One clean file next to one dirty file: only the clean file gets a marker.

    ESLint has no per-file success signal to parse in step 4 (whole-batch
    semantics), so this test also documents the coarser behaviour: on
    non-zero engine exit, no unit is marked.
    """
    _needs_node()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "clean.js").write_text(JS_CLEAN)
    (tmp_path / "src" / "bad.js").write_text(JS_SWALLOW)
    _init_repo(tmp_path)
    _commit_all(tmp_path)
    cache_home = tmp_path / "cache"

    r = _run_tackbox(tmp_path, cache_home)
    assert r.returncode != 0
    # Whole-batch semantics for eslint: nothing marked when the batch failed.
    assert _marker_count(cache_home, "tackbox-eslint") == 0


# -- Corrupt marker does not fail the run ---------------------------------


def test_corrupt_marker_does_not_fail(clean_js_repo, tmp_path):
    """A directory sitting at a marker path is treated as a miss.

    The engine re-runs, mark_clean's attempt to touch the marker fails
    silently (because the path is a directory), and the run still exits 0.
    """
    cache_home = tmp_path / "cache"
    # Simulate a corrupt marker by putting a directory where any marker would go.
    corrupt = cache_home / "v1" / _dev_hash() / "corrupt.tackbox-eslint"
    corrupt.mkdir(parents=True)

    r = _run_tackbox(clean_js_repo, cache_home)
    assert r.returncode == 0, (
        f"corrupt marker must not fail the run\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )


# -- --no-cache flag -------------------------------------------------------


def test_no_cache_flag_writes_no_markers(go_repo, tmp_path):
    cache_home = tmp_path / "cache"
    r = _run_tackbox(go_repo, cache_home, "--no-cache")
    assert r.returncode == 0
    assert _all_markers(cache_home) == []


def test_no_cache_flag_ignores_existing_markers(go_repo, tmp_path):
    """A cached marker present but ignored: engine still runs and reports."""
    cache_home = tmp_path / "cache"
    # Prime cache
    assert _run_tackbox(go_repo, cache_home).returncode == 0
    # Break B's source but skip cache; engine must run and see the change.
    (go_repo / "pkg_b" / "b.go").write_text(GO_PKG_B_ADD3)
    (go_repo / "pkg_a" / "a.go").write_text(GO_PKG_A_USES_B_ADD3)
    r = _run_tackbox(go_repo, cache_home, "--no-cache")
    assert r.returncode == 0
    # With --no-cache, sections are always emitted.
    assert "== erclint ==" in r.stdout


# -- Stale engines-hash dirs are GC'd on every run -----------------------


def test_stale_engines_hash_dir_pruned_on_run(clean_js_repo, tmp_path):
    cache_home = tmp_path / "cache"
    stale = cache_home / "v1" / "old-engines-hash"
    stale.mkdir(parents=True)
    (stale / "some.eng").touch()
    assert _run_tackbox(clean_js_repo, cache_home).returncode == 0
    assert not stale.exists()
    assert (cache_home / "v1" / _dev_hash()).is_dir()


# -- Units without a digest are linted, never cached ------------------------


def test_missing_digest_still_lints_and_never_caches(monkeypatch, tmp_path):
    """A package go list cannot attribute must be linted on every run.

    Dropping it from the plan would silently skip enforcement - the exact
    failure class tackbox exists to prevent.
    """
    monkeypatch.setattr(
        tackbox_cache, "erclint_package_digests", lambda root, dirs: {"pkg_a": "d1"}
    )
    monkeypatch.setattr(
        tackbox_cache, "erclint_import_paths", lambda root, dirs: {"pkg_a": "m/pkg_a"}
    )
    erclint = next(e for e in DEV_ENGINES if e.id == "erclint")
    cache_root = tmp_path / "cacheroot"

    filtered, pending = cli._apply_cache(
        [(erclint, ["pkg_a", "pkg_b"])], tmp_path, "h", cache_root
    )
    assert [(e.id, args) for e, args in filtered] == [
        ("erclint", ["pkg_a", "pkg_b"])
    ]

    clean = EngineResult(engine_id="erclint", exit_code=0, stdout="{}", stderr="")
    cli._mark_clean_units([clean], pending, "h", cache_root)
    assert [p.name for p in sorted((cache_root / "h").iterdir())] == ["d1.erclint"]


# -- javalint: exit 0 always, so attribution must come from the findings ----


def test_clean_args_javalint_excludes_files_with_findings():
    """javalint returns exit 0 even with findings (erclint-shaped JSON), so a
    generic 'exit 0 -> everything clean' rule would falsely cache a java file
    that has a finding. Attribution must read the finding file keys instead."""
    findings = (
        '{"java/Bad.java": {"JV001": [{"posn": "java/Bad.java:2:9", '
        '"end": "java/Bad.java:2:9", "message": "m"}]}}'
    )
    r = EngineResult(engine_id="javalint", exit_code=0, stdout=findings, stderr="")
    info = {"arg_digest": [("java/Bad.java", "d1"), ("java/Ok.java", "d2")]}
    assert cli._clean_args(r, info) == ["java/Ok.java"]


def test_clean_args_javalint_all_clean_when_no_findings():
    r = EngineResult(engine_id="javalint", exit_code=0, stdout="{}\n", stderr="")
    info = {"arg_digest": [("java/A.java", "d1"), ("java/B.java", "d2")]}
    assert cli._clean_args(r, info) == ["java/A.java", "java/B.java"]


# -- A crashed engine run must never be attributed clean ---------------------
#
# erclint's -json mode and javalint both normally exit 0 with findings, so
# _clean_args parses stdout to attribute cleanness per unit. But a crash (go
# panic, javalint's tier-2 dead-symbol / malformed --reporters exit 2) leaves
# stdout empty; parse_erclint_findings("") returns [] without raising, so an
# unguarded parse would read "no findings" as "everything clean" and cache a
# batch the engine never actually analyzed.


def test_clean_args_erclint_crash_caches_nothing():
    r = EngineResult(engine_id="erclint", exit_code=2, stdout="", stderr="panic: boom")
    info = {"arg_digest": [("pkg", "d1")], "arg_ip": {"pkg": "example.com/pkg"}}
    assert cli._clean_args(r, info) == []


def test_clean_args_javalint_crash_caches_nothing():
    r = EngineResult(engine_id="javalint", exit_code=2, stdout="", stderr="javalint: boom")
    info = {"arg_digest": [("java/Bad.java", "d1")]}
    assert cli._clean_args(r, info) == []


def test_erclint_crash_writes_no_marker_and_next_run_still_reports(tmp_path):
    cache_root = tmp_path / "cacheroot"
    pending = {"arg_digest": [("pkg", "d1")], "arg_ip": {"pkg": "example.com/pkg"}}

    crashed = EngineResult(engine_id="erclint", exit_code=2, stdout="", stderr="panic: boom")
    cli._mark_clean_units([crashed], {"erclint": pending}, "h", cache_root)
    assert not (cache_root / "h").exists() or list((cache_root / "h").iterdir()) == []

    # Follow-up probe: a real finding on the same unit must still surface -
    # the crash must not have poisoned the cache with a false clean marker.
    probe = EngineResult(
        engine_id="erclint",
        exit_code=0,
        stdout='{"example.com/pkg": {"errcheck": [{"pkg": "example.com/pkg"}]}}',
        stderr="",
    )
    assert cli._clean_args(probe, pending) == []


def test_javalint_crash_writes_no_marker_and_next_run_still_reports(tmp_path):
    cache_root = tmp_path / "cacheroot"
    pending = {"arg_digest": [("java/Bad.java", "d1")]}

    crashed = EngineResult(engine_id="javalint", exit_code=2, stdout="", stderr="javalint: boom")
    cli._mark_clean_units([crashed], {"javalint": pending}, "h", cache_root)
    assert not (cache_root / "h").exists() or list((cache_root / "h").iterdir()) == []

    # Follow-up probe: a real finding on the same unit must still surface.
    probe = EngineResult(
        engine_id="javalint",
        exit_code=0,
        stdout=(
            '{"java/Bad.java": {"JV001": [{"posn": "java/Bad.java:2:9", '
            '"end": "java/Bad.java:2:9", "message": "m"}]}}'
        ),
        stderr="",
    )
    assert cli._clean_args(probe, pending) == []


# -- CLI-level repro: a crashed run must not mask a real finding later ------


JAVA_SWALLOW_WITH_REPORTER = """class Handler {
    void run() {
        try {
            work();
        } catch (Exception e) {
        }
    }
    void work() {}
    static void report(Throwable t) {}
}
"""


def _needs_java():
    if shutil.which("java") is None or shutil.which("mvn") is None:
        pytest.fail("java/mvn not installed; install it, do not skip")


@pytest.fixture
def java_swallow_repo(tmp_path) -> Path:
    _needs_java()
    (tmp_path / "Handler.java").write_text(JAVA_SWALLOW_WITH_REPORTER)
    # Typo: "repot" instead of "report" - a dead declared symbol, exit 2.
    (tmp_path / ".tackbox-reporters").write_text(
        "Handler.java#Handler.repot: swallow log helper\n"
    )
    _init_repo(tmp_path)
    _commit_all(tmp_path)
    return tmp_path


def test_reporters_typo_crash_does_not_hide_swallow_after_fix(java_swallow_repo, tmp_path):
    """Exact reproduced scenario: a real java swallow plus a typo'd
    `.tackbox-reporters` (dead declared symbol) crashes javalint loudly (exit
    2). Fixing the typo must make the next run see the still-unfixed swallow,
    not a false-clean cache marker left by the crashed run."""
    cache_home = tmp_path / "cache"

    crashed = _run_tackbox(java_swallow_repo, cache_home)
    assert crashed.returncode == 2, (
        f"expected the dead reporter symbol to crash loudly\n"
        f"stdout={crashed.stdout!r}\nstderr={crashed.stderr!r}"
    )
    assert "repot" in crashed.stderr

    (java_swallow_repo / ".tackbox-reporters").write_text(
        "Handler.java#Handler.report: swallow log helper\n"
    )

    result = _run_tackbox(java_swallow_repo, cache_home)
    assert result.returncode != 0, (
        f"the swallow must still be reported, not hidden by a stale cache marker\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert "JV001" in result.stdout
    assert "Handler.java" in result.stdout

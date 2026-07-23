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

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import commit_all, init_repo, run_lint, tackbox_env

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
    root = cache_home / tackbox_cache.CACHE_VERSION / _dev_hash()
    if not root.is_dir():
        return 0
    return sum(1 for p in root.iterdir() if p.name.endswith(f".{engine_id}"))


def _all_markers(cache_home: Path) -> list[Path]:
    root = cache_home / tackbox_cache.CACHE_VERSION / _dev_hash()
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
    init_repo(tmp_path)
    commit_all(tmp_path)
    return tmp_path


@pytest.fixture
def clean_js_repo(tmp_path) -> Path:
    _needs_node()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text(JS_CLEAN)
    (tmp_path / "src" / "b.js").write_text(JS_CLEAN)
    init_repo(tmp_path)
    commit_all(tmp_path)
    return tmp_path


@pytest.fixture
def dirty_js_repo(tmp_path) -> Path:
    _needs_node()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bad.js").write_text(JS_SWALLOW)
    init_repo(tmp_path)
    commit_all(tmp_path)
    return tmp_path


# -- Warm/cold cache -------------------------------------------------------


def test_cold_run_writes_markers(go_repo, tmp_path):
    cache_home = tmp_path / "cache"
    result = run_lint(go_repo, cache_home)
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
    prime = run_lint(go_repo, tmp_path / "cache")
    assert prime.returncode == 0
    warm = run_lint(go_repo, cache_home)
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
    assert run_lint(go_repo, cache_home).returncode == 0
    before = {p.name for p in _all_markers(cache_home)}
    erclint_before = {p.name for p in _all_markers(cache_home) if p.name.endswith(".erclint")}

    (go_repo / "pkg_b" / "b.go").write_text(GO_PKG_B_ADD3)
    (go_repo / "pkg_a" / "a.go").write_text(GO_PKG_A_USES_B_ADD3)

    result = run_lint(go_repo, cache_home)
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
    assert run_lint(go_repo, cache_home).returncode == 0
    erclint_before = {p.name for p in _all_markers(cache_home) if p.name.endswith(".erclint")}

    (go_repo / "pkg_c" / "c.go").write_text(GO_PKG_C_UNRELATED + "\n// tail\n")
    assert run_lint(go_repo, cache_home).returncode == 0

    erclint_after = {p.name for p in _all_markers(cache_home) if p.name.endswith(".erclint")}
    new_marks = erclint_after - erclint_before
    # Exactly one new marker: pkg_c's fresh digest. pkg_a and pkg_b untouched.
    assert len(new_marks) == 1


# -- Failures are not cached ----------------------------------------------


def test_failure_not_cached(dirty_js_repo, tmp_path):
    cache_home = tmp_path / "cache"
    r = run_lint(dirty_js_repo, cache_home)
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
    init_repo(tmp_path)
    commit_all(tmp_path)
    cache_home = tmp_path / "cache"

    r = run_lint(tmp_path, cache_home)
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
    corrupt = cache_home / tackbox_cache.CACHE_VERSION / _dev_hash() / "corrupt.tackbox-eslint"
    corrupt.mkdir(parents=True)

    r = run_lint(clean_js_repo, cache_home)
    assert r.returncode == 0, (
        f"corrupt marker must not fail the run\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )


# -- --no-cache flag -------------------------------------------------------


def test_no_cache_flag_writes_no_markers(go_repo, tmp_path):
    cache_home = tmp_path / "cache"
    r = run_lint(go_repo, cache_home, "--no-cache")
    assert r.returncode == 0
    assert _all_markers(cache_home) == []


def test_no_cache_flag_ignores_existing_markers(go_repo, tmp_path):
    """A cached marker present but ignored: engine still runs and reports."""
    cache_home = tmp_path / "cache"
    # Prime cache
    assert run_lint(go_repo, cache_home).returncode == 0
    # Break B's source but skip cache; engine must run and see the change.
    (go_repo / "pkg_b" / "b.go").write_text(GO_PKG_B_ADD3)
    (go_repo / "pkg_a" / "a.go").write_text(GO_PKG_A_USES_B_ADD3)
    r = run_lint(go_repo, cache_home, "--no-cache")
    assert r.returncode == 0
    # With --no-cache, sections are always emitted.
    assert "== erclint ==" in r.stdout


# -- Stale engines-hash dirs are GC'd on every run -----------------------


def test_stale_engines_hash_dir_pruned_on_run(clean_js_repo, tmp_path):
    cache_home = tmp_path / "cache"
    stale = cache_home / tackbox_cache.CACHE_VERSION / "old-engines-hash"
    stale.mkdir(parents=True)
    (stale / "some.eng").touch()
    assert run_lint(clean_js_repo, cache_home).returncode == 0
    assert not stale.exists()
    assert (cache_home / tackbox_cache.CACHE_VERSION / _dev_hash()).is_dir()


# -- Units without a digest are linted, never cached ------------------------


def test_missing_digest_still_lints_and_never_caches(monkeypatch, tmp_path):
    """A package go list cannot attribute must be linted on every run.

    Dropping it from the plan would silently skip enforcement - the exact
    failure class tackbox exists to prevent.
    """
    monkeypatch.setattr(
        tackbox_cache, "erclint_package_digests", lambda root, dirs, policy: {"pkg_a": "d1"}
    )
    monkeypatch.setattr(
        tackbox_cache, "erclint_import_paths", lambda root, dirs: {"pkg_a": "m/pkg_a"}
    )
    erclint = next(e for e in DEV_ENGINES if e.id == "erclint")
    cache_root = tmp_path / "cacheroot"

    filtered, pending = cli._apply_cache(
        [(erclint, ["pkg_a", "pkg_b"])], tmp_path, "h", cache_root, "p"
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
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox/reporters").write_text(
        "Handler.java#Handler.repot: swallow log helper\n"
    )
    init_repo(tmp_path)
    commit_all(tmp_path)
    return tmp_path


def test_reporters_typo_crash_does_not_hide_swallow_after_fix(java_swallow_repo, tmp_path):
    """Exact reproduced scenario: a real java swallow plus a typo'd
    `.tackbox/reporters` (dead declared symbol) crashes javalint loudly (exit
    2). Fixing the typo must make the next run see the still-unfixed swallow,
    not a false-clean cache marker left by the crashed run."""
    cache_home = tmp_path / "cache"

    crashed = run_lint(java_swallow_repo, cache_home)
    assert crashed.returncode == 2, (
        f"expected the dead reporter symbol to crash loudly\n"
        f"stdout={crashed.stdout!r}\nstderr={crashed.stderr!r}"
    )
    assert "repot" in crashed.stderr

    (java_swallow_repo / ".tackbox/reporters").write_text(
        "Handler.java#Handler.report: swallow log helper\n"
    )

    result = run_lint(java_swallow_repo, cache_home)
    assert result.returncode != 0, (
        f"the swallow must still be reported, not hidden by a stale cache marker\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert "JV001" in result.stdout
    assert "Handler.java" in result.stdout


# -- A1: cache-key soundness regressions ----------------------------------
#
# Each primes a clean warm cache, then changes an input the old (unit, engine)
# key ignored - reporter policy, unit path, or a _test.go file - and asserts the
# warm run now reports. Before the v2 key these three returned stale-clean.


def _run_scope(
    repo: Path, cache_home: Path, scope: str, *extra: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "lint", scope, *extra],
        cwd=repo,
        env=tackbox_env(TACKBOX_CACHE_HOME=str(cache_home)),
        capture_output=True,
        text=True,
    )


PY_SWALLOW_WITH_REPORTER = '''def report_it(msg, e):
    print(msg, e)


def handler():
    try:
        work()
    except ValueError as e:
        report_it("handled the failure", e)


def work():
    pass
'''


def test_reporters_removal_invalidates_warm_cache(tmp_path):
    """A1(a): a swallow credited by .tackbox/reporters is primed clean; deleting
    the declaration must make the warm run report TBX001, not serve a stale clean
    marker keyed without the reporter policy."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(PY_SWALLOW_WITH_REPORTER)
    (repo / ".tackbox").mkdir()
    (repo / ".tackbox/reporters").write_text(
        "mod.py#report_it: local py swallow sink\n"
    )
    init_repo(repo)
    commit_all(repo)
    cache_home = tmp_path / "cache"

    prime = run_lint(repo, cache_home)
    assert prime.returncode == 0, (
        f"declared reporter should make the swallow clean:\n"
        f"{prime.stdout}\n{prime.stderr}"
    )

    (repo / ".tackbox/reporters").unlink()
    warm = run_lint(repo, cache_home)
    assert warm.returncode != 0, (
        f"deleting the reporter must surface the swallow, not a stale clean:\n"
        f"{warm.stdout}\n{warm.stderr}"
    )
    assert "TBX001" in warm.stdout, warm.stdout


PY_BROAD_NOTIFY = '''from tackbox_report import notify


def handle():
    try:
        work()
    except Exception as e:
        notify("the user lost connectivity here", e, {"area": "net"}, "net.conn")


def work():
    pass
'''


def test_test_exemption_does_not_leak_to_prod_path(tmp_path):
    """A1(b): byte-identical content is test-exempt at a tests/ path but a TBX010
    finding at a production path. Priming on the test file must not serve its
    clean marker for the identical-content production file - the old key omitted
    the unit path."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "tests" / "test_app.py").write_text(PY_BROAD_NOTIFY)
    (repo / "src" / "app.py").write_text(PY_BROAD_NOTIFY)
    init_repo(repo)
    commit_all(repo)
    cache_home = tmp_path / "cache"

    prime = _run_scope(repo, cache_home, "tests/test_app.py")
    assert prime.returncode == 0, (
        f"the notify is test-exempt, so the test file primes clean:\n"
        f"{prime.stdout}\n{prime.stderr}"
    )

    warm = _run_scope(repo, cache_home, "src/app.py")
    assert warm.returncode != 0, (
        f"identical content at a production path must report, not inherit the "
        f"test file's clean marker:\n{warm.stdout}\n{warm.stderr}"
    )
    assert "TBX010" in warm.stdout, warm.stdout


GO_MOD_SKIP = "module skipfixture\n\ngo 1.24\n"

GO_SKIP_SRC = """package pkg

func Add(a, b int) int { return a + b }
"""

GO_TEST_CLEAN = """package pkg

import "testing"

func TestAdd(t *testing.T) {
\tif Add(1, 1) != 2 {
\t\tt.Fatal("bad sum")
\t}
}
"""

GO_TEST_SKIP = """package pkg

import "testing"

func TestAdd(t *testing.T) {
\tt.Skip()
}
"""


def test_test_go_change_invalidates_erclint_cache(tmp_path):
    """A1(c): a bare t.Skip() added to a _test.go must surface ERC008 on the warm
    run. The old package digest hashed only GoFiles, so a _test.go edit was
    invisible and the warm run stayed stale-clean."""
    _needs_go()
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "go.mod").write_text(GO_MOD_SKIP)
    (repo / "pkg" / "mod.go").write_text(GO_SKIP_SRC)
    (repo / "pkg" / "mod_test.go").write_text(GO_TEST_CLEAN)
    init_repo(repo)
    commit_all(repo)
    cache_home = tmp_path / "cache"

    prime = run_lint(repo, cache_home)
    assert prime.returncode == 0, (
        f"clean go package should prime clean:\n{prime.stdout}\n{prime.stderr}"
    )

    (repo / "pkg" / "mod_test.go").write_text(GO_TEST_SKIP)
    warm = run_lint(repo, cache_home)
    assert warm.returncode != 0, (
        f"a skip added to a _test.go must report, not serve a stale clean:\n"
        f"{warm.stdout}\n{warm.stderr}"
    )
    assert "ERC008" in warm.stdout, warm.stdout


# -- Test-file findings must not write a false-clean marker ------------------
#
# erclint keys a _test.go finding under a `.test` package variant - `pkg
# [pkg.test]` for an in-package test, `pkg_test [pkg.test]` for an external
# one - while the cache maps args to bare import paths (`pkg`). The base key
# never matched the suffixed finding key, so _clean_args judged the package
# clean and the reporting run wrote a false-clean marker; the next warm run
# served it. A1(c) above missed this because its single warm run is the
# digest-invalidated cold run, not a run served from the poisoned marker.
# These drive repeated runs off ONE unchanged tree: the finding is present
# from the start, so the very first (priming) run reports it, and both warm
# runs must keep reporting - none may write a false clean.

GO_MOD_SKIPNOW = "module skipnowfixture\n\ngo 1.24\n"

GO_SRC_TRIVIAL = """package pkg

func Add(a, b int) int { return a + b }
"""

# In-package test -> erclint keys the ERC008 finding under `pkg [pkg.test]`.
GO_TEST_SKIPNOW_INPKG = """package pkg

import "testing"

func TestAdd(t *testing.T) {
\tt.SkipNow()
}
"""

# External test package -> erclint keys the finding under `pkg_test [pkg.test]`
# (the pre-bracket text is `pkg_test`, not the base; only the bracket content
# recovers `pkg`).
GO_TEST_SKIPNOW_EXT = """package pkg_test

import "testing"

func TestExt(t *testing.T) {
\tt.SkipNow()
}
"""


def _make_skipnow_repo(tmp_path: Path, test_file: str, test_src: str) -> tuple[Path, Path]:
    """A one-package Go repo (trivial source + one `_test.go`) and its cache
    home. `test_file`/`test_src` pick the test-file variant under test."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "go.mod").write_text(GO_MOD_SKIPNOW)
    (repo / "pkg" / "mod.go").write_text(GO_SRC_TRIVIAL)
    (repo / "pkg" / test_file).write_text(test_src)
    init_repo(repo)
    commit_all(repo)
    return repo, tmp_path / "cache"


def _assert_reports_across_runs(repo: Path, cache_home: Path, needle: str) -> None:
    # cold prime reports (and must not poison the cache); both warm runs must
    # still report off the unchanged, cached tree.
    for label in ("cold-prime", "warm-1", "warm-2"):
        r = run_lint(repo, cache_home)
        assert r.returncode != 0, (
            f"{label}: finding must report, not serve a false clean:\n"
            f"{r.stdout}\n{r.stderr}"
        )
        assert needle in r.stdout, f"{label}: {needle} missing:\n{r.stdout}"


def test_inpackage_test_finding_survives_warm_runs(tmp_path):
    """In-package `_test.go` ERC008 (keyed `pkg [pkg.test]`): the priming run
    must not write a false-clean marker, so both warm runs still report."""
    _needs_go()
    repo, cache_home = _make_skipnow_repo(tmp_path, "mod_test.go", GO_TEST_SKIPNOW_INPKG)
    _assert_reports_across_runs(repo, cache_home, "ERC008")


def test_external_test_finding_survives_warm_runs(tmp_path):
    """External `package pkg_test` `_test.go` ERC008 (keyed `pkg_test
    [pkg.test]`): the bracket-content normalization is what attributes it to
    `pkg`; without it the priming run false-cleans and warm runs stay silent."""
    _needs_go()
    repo, cache_home = _make_skipnow_repo(tmp_path, "mod_ext_test.go", GO_TEST_SKIPNOW_EXT)
    _assert_reports_across_runs(repo, cache_home, "ERC008")


def test_clean_go_package_still_caches_after_warm_run(tmp_path):
    """No over-broadening: a genuinely clean Go package (with a clean _test.go)
    must still cache - the warm run skips erclint entirely."""
    _needs_go()
    repo, cache_home = _make_skipnow_repo(tmp_path, "mod_test.go", GO_TEST_CLEAN)

    prime = run_lint(repo, cache_home)
    assert prime.returncode == 0, f"clean package must prime clean:\n{prime.stdout}\n{prime.stderr}"
    warm = run_lint(repo, cache_home)
    assert warm.returncode == 0, f"{warm.stdout}\n{warm.stderr}"
    assert "== erclint ==" not in warm.stdout, (
        f"a clean package must stay cached on the warm run:\n{warm.stdout}"
    )


# -- _clean_args: test-variant attribution (unit, no go toolchain) -----------


def test_clean_args_erclint_test_variant_marks_package_dirty():
    """Both `.test` finding-key variants must attribute back to the base
    package, so a _test.go finding never caches clean."""
    for key in ("m/foo [m/foo.test]", "m/foo_test [m/foo.test]"):
        r = EngineResult(
            engine_id="erclint",
            exit_code=0,
            stdout='{"%s": {"skiptest": [{"posn": "x:1:1"}]}}' % key,
            stderr="",
        )
        info = {"arg_digest": [("foo", "d1")], "arg_ip": {"foo": "m/foo"}}
        assert cli._clean_args(r, info) == [], key


def test_clean_args_erclint_nontest_finding_attributes_per_package():
    """A normal (non-test) finding marks only its own package dirty; a clean
    sibling package must still be cached (the fix must not over-broaden)."""
    r = EngineResult(
        engine_id="erclint",
        exit_code=0,
        stdout='{"m/foo": {"errcheck": [{"posn": "x:1:1"}]}}',
        stderr="",
    )
    info = {
        "arg_digest": [("foo", "d1"), ("bar", "d2")],
        "arg_ip": {"foo": "m/foo", "bar": "m/bar"},
    }
    assert cli._clean_args(r, info) == ["bar"]

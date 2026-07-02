"""Unit tests for the (unit, engine) cache module.

These pin the plan's cache semantics before implementation:

- Layout: `<root>/v1/<engines-hash>/<unit-digest>.<engine-id>`.
- Marker is an empty file; `is_cached` re-touches on hit for LRU.
- `mark_clean` never raises; corruption -> rerun, not fail.
- `gc_stale_engines` drops non-current engines-hash dirs.
- `gc_soft_cap` drops oldest markers within the current dir once the cap
  is exceeded.
- erclint unit digest = import-path + own `.go` files + transitive
  in-module deps' `.go` files + go.mod + go.sum. Same-only-if-source-equal.

End-to-end CLI cache tests live in test_cli_cache.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tackbox import cache


# -- Layout ----------------------------------------------------------------


def test_cache_key_marker_layout(tmp_path):
    key = cache.CacheKey(engines_hash="e1", unit_digest="ab12", engine_id="erclint")
    assert key.marker(tmp_path) == tmp_path / "e1" / "ab12.erclint"


def _payload_tree(root: Path) -> None:
    (root / "go" / "analyzers").mkdir(parents=True)
    (root / "go" / "analyzers" / "a.go").write_text("package a\n")
    (root / "js" / "rules").mkdir(parents=True)
    (root / "js" / "rules" / "r.js").write_text("rule\n")
    (root / "bin").mkdir()
    (root / "bin" / "t.js").write_text("wrapper\n")
    (root / "eslint.config.preset.js").write_text("preset\n")
    (root / "package.json").write_text("{}\n")
    (root / "package-lock.json").write_text("{}\n")
    (root / "py").mkdir()
    (root / "py" / "cli.py").write_text("orchestrator\n")


def test_engines_hash_dev_is_deterministic(tmp_path):
    _payload_tree(tmp_path)
    assert cache.engines_hash_dev(tmp_path) == cache.engines_hash_dev(tmp_path)


def test_engines_hash_dev_changes_when_rule_source_changes(tmp_path):
    _payload_tree(tmp_path)
    before = cache.engines_hash_dev(tmp_path)
    (tmp_path / "js" / "rules" / "r.js").write_text("edited rule\n")
    assert cache.engines_hash_dev(tmp_path) != before


def test_engines_hash_dev_ignores_orchestrator_sources(tmp_path):
    _payload_tree(tmp_path)
    before = cache.engines_hash_dev(tmp_path)
    (tmp_path / "py" / "cli.py").write_text("edited orchestrator\n")
    assert cache.engines_hash_dev(tmp_path) == before


# -- is_cached / mark_clean ------------------------------------------------


def test_is_cached_missing_returns_false(tmp_path):
    key = cache.CacheKey("e1", "d1", "eng")
    assert cache.is_cached(key, tmp_path) is False


def test_is_cached_hit_returns_true_and_touches_mtime(tmp_path):
    key = cache.CacheKey("e1", "d1", "eng")
    p = key.marker(tmp_path)
    p.parent.mkdir(parents=True)
    p.touch()
    old = p.stat().st_mtime
    # Backdate so touch is observable regardless of filesystem mtime resolution.
    os.utime(p, (old - 3600, old - 3600))
    assert cache.is_cached(key, tmp_path) is True
    assert p.stat().st_mtime > old - 3600


def test_is_cached_directory_at_marker_path_is_treated_as_miss(tmp_path):
    key = cache.CacheKey("e1", "d1", "eng")
    p = key.marker(tmp_path)
    p.mkdir(parents=True)
    assert cache.is_cached(key, tmp_path) is False


def test_mark_clean_creates_parent_and_marker(tmp_path):
    key = cache.CacheKey("e1", "d1", "eng")
    cache.mark_clean(key, tmp_path)
    assert key.marker(tmp_path).is_file()


def test_mark_clean_swallows_when_marker_path_is_directory(tmp_path):
    key = cache.CacheKey("e1", "d1", "eng")
    key.marker(tmp_path).mkdir(parents=True)
    cache.mark_clean(key, tmp_path)  # must not raise


def test_mark_clean_swallows_when_parent_is_file(tmp_path):
    key = cache.CacheKey("e1", "d1", "eng")
    parent = key.marker(tmp_path).parent
    parent.parent.mkdir(parents=True, exist_ok=True)
    parent.write_bytes(b"")  # file where we expected a directory
    cache.mark_clean(key, tmp_path)  # must not raise


# -- GC --------------------------------------------------------------------


def test_gc_stale_engines_removes_non_current_dirs(tmp_path):
    (tmp_path / "old-hash-1").mkdir()
    (tmp_path / "old-hash-2").mkdir()
    (tmp_path / "current-hash").mkdir()
    cache.gc_stale_engines("current-hash", tmp_path)
    assert (tmp_path / "current-hash").is_dir()
    assert not (tmp_path / "old-hash-1").exists()
    assert not (tmp_path / "old-hash-2").exists()


def test_gc_stale_engines_no_op_when_root_missing(tmp_path):
    missing = tmp_path / "not-there"
    cache.gc_stale_engines("x", missing)  # must not raise


def test_gc_stale_engines_ignores_stray_files(tmp_path):
    (tmp_path / "e1").mkdir()
    (tmp_path / "stray").write_bytes(b"junk")
    cache.gc_stale_engines("e1", tmp_path)
    assert (tmp_path / "stray").is_file()  # untouched
    assert (tmp_path / "e1").is_dir()


def test_gc_soft_cap_keeps_newest_and_drops_oldest(tmp_path):
    d = tmp_path / "eng-hash"
    d.mkdir()
    marks = []
    now = time.time()
    for i in range(6):
        p = d / f"mark{i}.eng"
        p.touch()
        os.utime(p, (now - (6 - i) * 100, now - (6 - i) * 100))
        marks.append(p)
    cache.gc_soft_cap("eng-hash", cap=3, root=tmp_path)
    remaining = sorted(p.name for p in d.iterdir())
    # marks 3,4,5 are newest by mtime; 0-2 are dropped
    assert remaining == ["mark3.eng", "mark4.eng", "mark5.eng"]


def test_gc_soft_cap_no_op_under_cap(tmp_path):
    d = tmp_path / "eng-hash"
    d.mkdir()
    (d / "a.eng").touch()
    (d / "b.eng").touch()
    cache.gc_soft_cap("eng-hash", cap=5, root=tmp_path)
    assert {p.name for p in d.iterdir()} == {"a.eng", "b.eng"}


def test_gc_soft_cap_no_op_when_dir_missing(tmp_path):
    cache.gc_soft_cap("nope", cap=5, root=tmp_path)  # must not raise


def test_gc_soft_cap_survives_files_vanishing_mid_scan(monkeypatch, tmp_path):
    d = tmp_path / "dev"
    d.mkdir()
    for i in range(3):
        (d / f"m{i}.eng").touch()
    victim = "m1.eng"
    real_stat = Path.stat
    calls = {"n": 0}

    def flaky_stat(self, **kwargs):
        # First stat (iterdir/is_file) sees the file; the sort-key stat
        # simulates a concurrent run unlinking it mid-scan.
        if self.name == victim:
            calls["n"] += 1
            if calls["n"] > 1:
                raise FileNotFoundError(victim)
        return real_stat(self, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    cache.gc_soft_cap("dev", cap=1, root=tmp_path)  # must not raise
    monkeypatch.undo()
    assert len([p for p in d.iterdir() if p.is_file()]) == 1


# -- File digest -----------------------------------------------------------


def test_sha256_file_matches_hashlib(tmp_path):
    p = tmp_path / "x"
    p.write_bytes(b"hello world")
    import hashlib

    assert cache.sha256_file(p) == hashlib.sha256(b"hello world").hexdigest()


# -- erclint package digest (needs `go` toolchain) --------------------------

_GO_MOD_DIGEST = """module cachefixture

go 1.24
"""

_PKG_B_ADD2 = """package pkg_b

func Add(a, b int) int { return a + b }
"""

_PKG_B_ADD3 = """package pkg_b

func Add(a, b, c int) int { return a + b + c }
"""

_PKG_A_USES_B = """package pkg_a

import "cachefixture/pkg_b"

func Two() int { return pkg_b.Add(1, 1) }
"""

_PKG_A_USES_B_ADD3 = """package pkg_a

import "cachefixture/pkg_b"

func Two() int { return pkg_b.Add(1, 1, 1) }
"""

_PKG_C_UNRELATED = """package pkg_c

func Ping() int { return 42 }
"""


def _needs_go():
    # Conventions: no test skips - a missing toolchain is an environment
    # bug to fix, not a reason to silently shrink coverage.
    if shutil.which("go") is None:
        pytest.fail("go toolchain not installed; install it, do not skip")


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def _make_go_repo(root: Path) -> None:
    (root / "go.mod").write_text(_GO_MOD_DIGEST)
    (root / "pkg_b").mkdir()
    (root / "pkg_b" / "b.go").write_text(_PKG_B_ADD2)
    (root / "pkg_a").mkdir()
    (root / "pkg_a" / "a.go").write_text(_PKG_A_USES_B)
    (root / "pkg_c").mkdir()
    (root / "pkg_c" / "c.go").write_text(_PKG_C_UNRELATED)
    _init_repo(root)


def test_erclint_digest_deterministic(tmp_path):
    _needs_go()
    _make_go_repo(tmp_path)
    d1 = cache.erclint_package_digests(tmp_path, ["pkg_a", "pkg_b"])
    d2 = cache.erclint_package_digests(tmp_path, ["pkg_a", "pkg_b"])
    assert d1 == d2
    assert set(d1) == {"pkg_a", "pkg_b"}


def test_erclint_digest_changes_when_own_files_change(tmp_path):
    _needs_go()
    _make_go_repo(tmp_path)
    before = cache.erclint_package_digests(tmp_path, ["pkg_b"])["pkg_b"]
    (tmp_path / "pkg_b" / "b.go").write_text(_PKG_B_ADD2 + "\n// tail\n")
    after = cache.erclint_package_digests(tmp_path, ["pkg_b"])["pkg_b"]
    assert before != after


def test_erclint_digest_of_dependent_changes_when_dep_changes(tmp_path):
    """Plan acceptance: signature change in B invalidates A (which imports B)."""
    _needs_go()
    _make_go_repo(tmp_path)
    before = cache.erclint_package_digests(tmp_path, ["pkg_a", "pkg_b"])
    (tmp_path / "pkg_b" / "b.go").write_text(_PKG_B_ADD3)
    (tmp_path / "pkg_a" / "a.go").write_text(_PKG_A_USES_B_ADD3)
    after = cache.erclint_package_digests(tmp_path, ["pkg_a", "pkg_b"])
    assert before["pkg_b"] != after["pkg_b"]
    assert before["pkg_a"] != after["pkg_a"], (
        "pkg_a depends on pkg_b - a signature change in B must invalidate A's digest"
    )


def test_erclint_digest_of_dependent_stable_when_unrelated_pkg_changes(tmp_path):
    _needs_go()
    _make_go_repo(tmp_path)
    before = cache.erclint_package_digests(tmp_path, ["pkg_a"])["pkg_a"]
    (tmp_path / "pkg_c" / "c.go").write_text(_PKG_C_UNRELATED + "\n// unrelated\n")
    after = cache.erclint_package_digests(tmp_path, ["pkg_a"])["pkg_a"]
    assert before == after, "unrelated pkg change must not touch pkg_a's digest"


def test_erclint_digest_changes_when_go_mod_changes(tmp_path):
    _needs_go()
    _make_go_repo(tmp_path)
    before = cache.erclint_package_digests(tmp_path, ["pkg_a"])["pkg_a"]
    (tmp_path / "go.mod").write_text(_GO_MOD_DIGEST + "\n// tail\n")
    after = cache.erclint_package_digests(tmp_path, ["pkg_a"])["pkg_a"]
    assert before != after

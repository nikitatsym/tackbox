"""CLI lint + approvals behavior under attribute exclusion (step 1 of the
generated-code-attributes plan).

Marked trees are generated inline into tmp git repos (never tracked fixtures)
so tackbox self-lint never scans them. The CLI runs as a real subprocess where
the exit code / console output is the assertion; the one-resolution and D012
fixtures run in-process (seam spy).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import commit_all, count_calls, init_repo, tackbox_env

from tackbox import cli, gitfiles

SUMMARY = "excluded by attributes: {n} files in scope (tackbox escapes lists all)"


def _needs_go():
    if shutil.which("go") is None:
        pytest.fail("go toolchain not installed; install it, do not skip")


def _needs_node():
    if shutil.which("node") is None:
        pytest.fail("node not installed; install it, do not skip")


# Python swallow -> TBX001 (python-swallowed-exception).
PY_SWALLOW = """def f():
    try:
        work()
    except ValueError as e:
        pass
"""

PY_CLEAN = """def g():
    return 1
"""

GO_MOD = "module attrfixture\n\ngo 1.24\n"

# ERC001 err-swallow.
GO_ERC001 = """package kan

import "errors"

func Fail() error {
\terr := errors.New("boom")
\tif err != nil {
\t\t_ = "swallowed"
\t}
\treturn errors.New("noop")
}
"""

GO_CLEAN = """package kan

func Ok() int { return 1 }
"""

# Type error: a value-less func cannot return an expression -> package won't
# compile, so erclint reports the whole package as compile-broken.
GO_BROKEN = """package kan

func Broken() { return notdefined }
"""


def _run(repo: Path, *args: str, cache_home: Path | None = None) -> subprocess.CompletedProcess:
    env = tackbox_env(**({"TACKBOX_CACHE_HOME": str(cache_home)} if cache_home else {}))
    return subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "lint", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def _build(tmp_path: Path, files: dict[str, str], attrs: str = "") -> Path:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    if attrs:
        (tmp_path / ".gitattributes").write_text(attrs, encoding="utf-8")
    init_repo(tmp_path, commit=True)
    return tmp_path


# -- findings in a marked file disappear + summary line -------------------


def test_marked_file_findings_disappear_and_summary_prints(tmp_path):
    repo = _build(
        tmp_path,
        {"gen/a.py": PY_SWALLOW, "src/b.py": PY_CLEAN},
        attrs="gen/*.py linguist-generated\n",
    )
    r = _run(repo, ".", "--no-cache")
    assert r.returncode == 0, f"marked swallow should not fail:\n{r.stdout}\n{r.stderr}"
    assert "gen/a.py" not in r.stdout
    assert "python-swallowed-exception" not in r.stdout
    assert SUMMARY.format(n=1) in r.stdout


def test_summary_two_attribute_file_counted_once(tmp_path):
    repo = _build(
        tmp_path,
        {"both.py": PY_CLEAN, "keep.py": PY_CLEAN},
        attrs="both.py linguist-generated gitlab-generated\n",
    )
    r = _run(repo, ".", "--no-cache")
    assert SUMMARY.format(n=1) in r.stdout


def test_summary_absent_when_scope_has_no_excluded(tmp_path):
    repo = _build(
        tmp_path,
        {"gen/a.py": PY_CLEAN, "src/b.py": PY_CLEAN},
        attrs="gen/*.py linguist-generated\n",
    )
    r = _run(repo, "src", "--no-cache")
    assert "excluded by attributes" not in r.stdout


def test_summary_reflects_only_current_scope(tmp_path):
    repo = _build(
        tmp_path,
        {"gen/a.py": PY_CLEAN, "other/gen2.py": PY_CLEAN, "src/c.py": PY_CLEAN},
        attrs="gen/*.py linguist-generated\nother/*.py linguist-generated\n",
    )
    scoped = _run(repo, "gen", "--no-cache")
    assert SUMMARY.format(n=1) in scoped.stdout
    whole = _run(repo, ".", "--no-cache")
    assert SUMMARY.format(n=2) in whole.stdout


# -- scope semantics -------------------------------------------------------


def test_scope_all_excluded_is_success_with_summary(tmp_path):
    # Candidates exist but are all excluded: success (ordinary verdict), summary
    # present - not the exit-2 "matched nothing".
    repo = _build(
        tmp_path,
        {"gen/a.py": PY_SWALLOW, "gen/b.py": PY_SWALLOW, "src/c.py": PY_CLEAN},
        attrs="gen/** linguist-generated\n",
    )
    r = _run(repo, "gen", "--no-cache")
    assert r.returncode == 0, f"all-excluded scope must be green:\n{r.stdout}\n{r.stderr}"
    assert SUMMARY.format(n=2) in r.stdout
    assert "python-swallowed-exception" not in r.stdout


def test_scope_matching_nothing_is_still_exit_2(tmp_path):
    repo = _build(tmp_path, {"src/a.py": PY_CLEAN}, attrs="")
    r = _run(repo, "does-not-exist")
    assert r.returncode == 2
    assert "matched no files" in r.stderr


def test_scoped_run_still_excludes(tmp_path):
    repo = _build(
        tmp_path,
        {"gen/a.py": PY_SWALLOW, "gen/keep.py": PY_SWALLOW},
        attrs="gen/a.py linguist-generated\n",
    )
    r = _run(repo, "gen", "--no-cache")
    # keep.py still linted (swallow finding present); a.py excluded.
    assert "gen/keep.py" in r.stdout
    assert "gen/a.py" not in r.stdout
    assert SUMMARY.format(n=1) in r.stdout


# -- one full-tree resolution (seam spy, in-process) ----------------------


def test_scoped_lint_performs_one_full_tree_resolution(tmp_path, monkeypatch):
    repo = _build(
        tmp_path,
        {"src/a.py": PY_CLEAN, "gen/g.py": PY_CLEAN},
        attrs="gen/*.py linguist-generated\n",
    )
    monkeypatch.chdir(repo)
    calls = count_calls(monkeypatch, gitfiles, "resolve_attributes")
    rc = cli._run_lint("src", no_cache=True, changed=False, since=None)
    assert rc == 0
    assert calls["n"] == 1, f"scoped lint + whole-tree approvals resolved {calls['n']} times"


# -- doctor CLI boundary: resolution failure is loud, not a traceback -----


def test_doctor_dispatch_resolution_failure_is_loud_not_traceback(
    tmp_path, monkeypatch, capsys
):
    # A genuine resolution failure inside doctor must surface as `tackbox: <msg>`
    # + exit 1 at the CLI boundary, never an uncaught traceback. The call
    # RETURNING (instead of raising) is the proof no traceback escapes.
    repo = _build(tmp_path, {"src/a.py": PY_CLEAN}, attrs="")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli, "_print_banner", lambda _root: None)

    def boom(*a, **k):
        raise gitfiles.AttributeResolutionError("check-attr exploded")

    monkeypatch.setattr(gitfiles, "resolve_attributes", boom)
    rc = cli._dispatch(["doctor"])
    assert rc == 1
    assert "tackbox: check-attr exploded" in capsys.readouterr().err


# -- D012 cascade: excluded file's marker orphans its manifest entry ------


def test_excluded_file_marker_orphans_manifest_entry(tmp_path, monkeypatch):
    marker = "no-report: boundary cleanup here, nothing to propagate onward"
    src = (
        "def f():\n"
        "    try:\n"
        "        work()\n"
        "    except ValueError as e:\n"
        f"        # {marker}\n"
        "        pass\n"
    )
    repo = _build(tmp_path, {"src/a.py": src}, attrs="")
    (repo / ".tackbox").mkdir()
    (repo / ".tackbox" / "approvals").write_text(f"src/a.py#f: {marker}\n")
    commit_all(repo)
    monkeypatch.chdir(repo)

    before = cli._approvals_report(repo)
    assert before.ok(), f"marked file covered by manifest should be consistent: {before}"

    (repo / ".gitattributes").write_text("src/a.py linguist-generated\n")
    commit_all(repo)
    after = cli._approvals_report(repo)
    assert not after.ok()
    assert [o.entry.address for o in after.orphans] == ["src/a.py#f"]


# -- codequality carries no entries for excluded files --------------------


def test_codequality_no_entries_for_excluded_files(tmp_path):
    repo = _build(
        tmp_path,
        {"gen/a.py": PY_SWALLOW, "src/b.py": PY_SWALLOW},
        attrs="gen/*.py linguist-generated\n",
    )
    report = repo / "cq.json"
    r = _run(repo, ".", "--no-cache", "--codequality", str(report))
    assert report.is_file()
    data = report.read_text()
    assert "gen/a.py" not in data
    assert "src/b.py" in data  # the included swallow still reported


# -- Go: warm-cache sequence + erclint post-filter + broken file ----------


def test_warm_cache_go_removing_attribute_brings_findings_back(tmp_path):
    _needs_go()
    cache_home = tmp_path / "cache"
    repo = _build(
        tmp_path / "repo",
        {"go.mod": GO_MOD, "kan/a.go": GO_ERC001, "kan/b.go": GO_CLEAN},
        attrs="kan/a.go linguist-generated\n",
    )
    # Marked: mixed package linted with cache on; the excluded file's ERC001 is
    # filtered from the verdict, and the package is NOT cached clean (raw truth).
    first = _run(repo, ".", cache_home=cache_home)
    assert first.returncode == 0, f"marked ERC should be green:\n{first.stdout}\n{first.stderr}"
    assert "ERC001" not in first.stdout

    # Remove the attribute, content untouched. Same cache home: the finding must
    # come back (the package was never cached clean).
    (repo / ".gitattributes").write_text("")
    commit_all(repo)
    second = _run(repo, ".", cache_home=cache_home)
    assert second.returncode == 1, f"unmarked ERC must fail:\n{second.stdout}\n{second.stderr}"
    assert "ERC001" in second.stdout
    assert "a.go" in second.stdout


def test_broken_excluded_file_in_mixed_package_fails_loudly(tmp_path):
    _needs_go()
    # Adversarial: an excluded file that breaks its dispatched mixed package must
    # still fail loudly - the compile-break path is not filtered.
    repo = _build(
        tmp_path,
        {"go.mod": GO_MOD, "kan/a.go": GO_BROKEN, "kan/b.go": GO_CLEAN},
        attrs="kan/a.go linguist-generated\n",
    )
    r = _run(repo, ".", "--no-cache")
    assert r.returncode != 0, (
        f"broken excluded file must fail the package:\n{r.stdout}\n{r.stderr}"
    )


# -- jscpd: a duplicate pair with one side marked draws no DUP ------------

_DUP_BODY = "\n".join(f"    total = total + step_{i} * {i}" for i in range(20))
_DUP_A = f"def compute():\n    total = 0\n{_DUP_BODY}\n    return total\n"
_DUP_B = f"def compute_two():\n    total = 0\n{_DUP_BODY}\n    return total\n"


def test_jscpd_pair_one_side_marked_draws_no_dup(tmp_path):
    _needs_node()
    # Control: without exclusion the pair is a real clone (DUP001 fires).
    control = _build(
        tmp_path / "control",
        {"dup/a.py": _DUP_A, "dup/b.py": _DUP_B},
        attrs="",
    )
    rc = _run(control, ".", "--no-cache")
    assert "DUP001" in rc.stdout, f"fixture is not a real duplicate:\n{rc.stdout}"

    marked = _build(
        tmp_path / "marked",
        {"dup/a.py": _DUP_A, "dup/b.py": _DUP_B},
        attrs="dup/b.py linguist-generated\n",
    )
    rm = _run(marked, ".", "--no-cache")
    assert "DUP001" not in rm.stdout, f"excluded side still drew a DUP:\n{rm.stdout}"
    assert SUMMARY.format(n=1) in rm.stdout

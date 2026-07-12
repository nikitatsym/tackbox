"""Step 3 acceptance: engine orchestration against a synthetic repo.

Materializes a tiny git repo with one seeded violation per engine, runs
`tackbox lint .` from it, and pins the aggregate exit code, the per-
engine sections, and the shape of the version banner.

Fixture files are generated inline (not stored as files under
`py/tests/fixtures/`) so tackbox self-lint never encounters them.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from _fixtures import PY_ONE_PER_RULE


GO_MOD = """module tackboxfixture

go 1.24
"""

GO_ERC001 = """package pkg

import "errors"

func Fail() error {
\terr := errors.New("boom")
\tif err != nil {
\t\t_ = "swallowed"
\t}
\treturn errors.New("noop")
}
"""

# ERC006 fingerprint via a tier-2 `.tackbox-reporters` sink: the secret-named
# arg reaches a declared reporter, so native erclint (not opengrep) must flag it.
GO_ERC006 = """package pkg

import "context"

func myReport(ctx context.Context, msg string, err error, tags map[string]string, key string) {}

func Trigger() {
\tvar authToken string
\tmyReport(context.Background(), authToken, nil, nil, "area.suffix")
}
"""

JS_SWALLOW = """try {
  doThing()
} catch (e) {
}
"""

MD_NON_ASCII = """# Notes

Some line with an em-dash: hello - world.
"""

PY_VIOLATIONS = PY_ONE_PER_RULE

# One violation per javalint rule; also pins .java dispatch to the javalint engine.
JAVA_VIOLATIONS = """class Violations {
    void swallowed() {
        try { work(); } catch (Exception e) {}
    }

    void catchThrowable() {
        try { work(); } catch (Throwable t) {}
    }

    void rethrowNoCause() {
        try { work(); } catch (Exception e) { throw new RuntimeException("wrapped call failed"); }
    }

    void useless() {
        try { work(); } catch (Exception e) { throw e; }
    }

    void exitInCatch() {
        try { work(); } catch (Exception e) { System.exit(1); }
    }
}
"""

# Swallows the recovered value: opengrep flags go-exit-in-recover, native
# erclint flags ERC007 recover-swallow.
GO_EXIT_IN_RECOVER = """package pkg

import "os"

func Recover() {
\tdefer func() {
\t\tif r := recover(); r != nil {
\t\t\tos.Exit(1)
\t\t}
\t}()
}
"""

# One violation per new eslint rule; short throw proves valid-throw-error is gone.
JS_VIOLATIONS = """function rethrow() {
  try { work() } catch (e) { throw new Error("connection dropped mid-call") }
}

function useless() {
  try { work() } catch (e) { throw e }
}

function exitInCatch() {
  try { work() } catch (e) { process.exit(1) }
}

function shortThrow() {
  throw new Error("x")
}
"""

# Negative: `# no-report: <reason>` with a reason suppresses the finding.
PY_SUPPRESSED_OK = """def cleanup():
    try:
        work()
    except ValueError as e:
        # no-report: boundary cleanup, nothing to propagate
        pass
"""

# Negative: `# no-report:` with an empty reason must NOT suppress.
PY_MARKER_NO_REASON = """def cleanup():
    try:
        work()
    except ValueError as e:
        # no-report:
        pass
"""

FIXTURE_MARKER = "<FIXTURE>"


@pytest.fixture(scope="module")
def fixture_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("goldenrepo")
    (root / "go.mod").write_text(GO_MOD)
    (root / "pkg").mkdir()
    (root / "pkg" / "swallow.go").write_text(GO_ERC001)
    (root / "pkg" / "secret.go").write_text(GO_ERC006)
    (root / ".tackbox-reporters").write_text("pkg/secret.go#myReport: fixture go sink\n")
    (root / "src").mkdir()
    (root / "src" / "swallow.js").write_text(JS_SWALLOW)
    (root / "docs").mkdir()
    # em-dash (U+2014) triggers no-non-ascii
    (root / "docs" / "notes.md").write_text("# Notes\n\nSome text — dash.\n")
    (root / "pkg" / "recover.go").write_text(GO_EXIT_IN_RECOVER)
    (root / "py").mkdir()
    (root / "py" / "violations.py").write_text(PY_VIOLATIONS)
    (root / "java").mkdir()
    (root / "java" / "Violations.java").write_text(JAVA_VIOLATIONS)
    (root / "src" / "violations.js").write_text(JS_VIOLATIONS)
    (root / "neg").mkdir()
    (root / "neg" / "suppressed_ok.py").write_text(PY_SUPPRESSED_OK)
    (root / "neg" / "marker_no_reason.py").write_text(PY_MARKER_NO_REASON)

    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _run_tackbox(fixture_repo: Path, *extra: str) -> subprocess.CompletedProcess:
    """Invoke the CLI in a subprocess so we exercise the real entrypoint.

    `--no-cache` keeps these golden tests deterministic across pytest runs -
    step 4's cache would otherwise skip engines with stable-clean units
    (e.g. opengrep on pkg/swallow.go in the scoped test).
    """
    tackbox_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tackbox_root / "py")
    return subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "lint", ".", "--no-cache", *extra],
        cwd=fixture_repo,
        env=env,
        capture_output=True,
        text=True,
    )


def _split_engine_sections(stdout: str) -> dict[str, str]:
    """Split combined stdout into `{engine_id: body}` by `== id ==` headers."""
    sections: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    for line in stdout.splitlines():
        m = re.match(r"^== (?P<id>[a-z\-]+) ==$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(body).rstrip() + "\n"
            current = m.group("id")
            body = []
        else:
            body.append(line)
    if current is not None:
        sections[current] = "\n".join(body).rstrip() + ("\n" if body else "")
    return sections


def _normalize(text: str, fixture_repo: Path) -> str:
    return text.replace(str(fixture_repo), FIXTURE_MARKER)


# --------- Acceptance ----------------------------------------------------


def test_exit_code_is_nonzero_when_any_engine_finds_violations(fixture_repo):
    result = _run_tackbox(fixture_repo)
    # eslint exits 1 on error; mdlint exits 1 on findings; erclint exits 0
    # (findings in JSON); opengrep exits 1 on --error findings. Aggregate = 1.
    assert result.returncode == 1, (
        f"expected 1, got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_banner_shape_on_stderr(fixture_repo):
    result = _run_tackbox(fixture_repo)
    banner = result.stderr.splitlines()[0]
    assert re.match(
        r"^tackbox \S+ engines=dev "
        r"erclint=\S+ opengrep=\S+ node=\S+ eslint=\S+ markdownlint=\S+$",
        banner,
    ), f"unexpected banner: {banner!r}"


def test_all_seven_engine_sections_present(fixture_repo):
    result = _run_tackbox(fixture_repo)
    sections = _split_engine_sections(result.stdout)
    assert set(sections) == {
        "erclint",
        "erclint-opengrep",
        "javalint",
        "pyrules",
        "tackbox-eslint",
        "tackbox-jscpd",
        "tackbox-mdlint",
    }


def test_erclint_reports_err_swallow_finding(fixture_repo):
    result = _run_tackbox(fixture_repo)
    section = _split_engine_sections(result.stdout)["erclint"]
    assert "errcheck" in section
    assert "ERC001" in section
    assert "swallow.go" in section


def test_erclint_reports_secret_arg(fixture_repo):
    result = _run_tackbox(fixture_repo)
    section = _split_engine_sections(result.stdout)["erclint"]
    assert "ERC006" in section
    assert "secret.go" in section


def test_eslint_reports_swallow_catch(fixture_repo):
    result = _run_tackbox(fixture_repo)
    section = _split_engine_sections(result.stdout)["tackbox-eslint"]
    assert "tackbox/no-swallow-catch" in section
    assert "swallow.js" in section


def test_mdlint_reports_non_ascii(fixture_repo):
    result = _run_tackbox(fixture_repo)
    section = _split_engine_sections(result.stdout)["tackbox-mdlint"]
    assert "MD-ASCII" in section or "no-non-ascii" in section
    assert "notes.md" in section


def test_engine_sections_appear_in_alphabetical_order(fixture_repo):
    """Deterministic ordering per engines.run_engines contract."""
    result = _run_tackbox(fixture_repo)
    ids = [
        m.group("id")
        for m in re.finditer(r"^== (?P<id>[a-z\-]+) ==$", result.stdout, re.M)
    ]
    assert ids == sorted(ids)
    assert ids == [
        "erclint",
        "erclint-opengrep",
        "javalint",
        "pyrules",
        "tackbox-eslint",
        "tackbox-jscpd",
        "tackbox-mdlint",
    ]


def test_scope_matching_no_files_is_error(fixture_repo):
    """Carry-forward from handover #3: never silently succeed on empty scope."""
    tackbox_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tackbox_root / "py")
    result = subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "lint", "does-not-exist"],
        cwd=fixture_repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "matched no files" in result.stderr


def test_pathspec_magic_scope_rejected(fixture_repo):
    tackbox_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tackbox_root / "py")
    result = subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "lint", "*.go"],
        cwd=fixture_repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "glob" in result.stderr.lower() or "pathspec" in result.stderr.lower()


def test_scoped_go_only_run_promotes_erclint_findings(fixture_repo):
    """erclint's `-json` mode exits 0 with findings; aggregate must be 1.

    Scope the run to pkg/swallow.go so eslint/mdlint drop out entirely.
    erclint, opengrep, and jscpd dispatch on the .go file; opengrep and
    jscpd (no intra-file clone) find nothing, so the nonzero aggregate must
    come from erclint promotion.
    """
    tackbox_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tackbox_root / "py")
    result = subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "lint", "pkg/swallow.go", "--no-cache"],
        cwd=fixture_repo,
        env=env,
        capture_output=True,
        text=True,
    )
    sections = _split_engine_sections(result.stdout)
    # eslint / mdlint dropped: no matching files in scope.
    assert set(sections) == {"erclint", "erclint-opengrep", "tackbox-jscpd"}
    assert result.returncode == 1, (
        f"expected 1, got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert "ERC001" in sections["erclint"]


# --------- Migrated / new rules (step B1) --------------------------------


def _nows(text: str) -> str:
    # Opengrep wraps long finding paths / rule ids across lines; collapse
    # whitespace so they still match (cf. stripWhitespace in the go wrapper test).
    return "".join(text.split())


@pytest.fixture(scope="module")
def sections(fixture_repo) -> dict[str, str]:
    return _split_engine_sections(_run_tackbox(fixture_repo).stdout)


def test_pyrules_reports_every_python_rule(sections):
    py = _nows(sections["pyrules"])
    for rule in (
        "python-swallowed-exception",
        "python-bare-except",
        "python-reraise-without-cause",
        "python-useless-except",
        "python-exit-in-except",
        "python-suppress-exception",
        "python-import-inside-function",
    ):
        assert rule in py, f"missing {rule} in pyrules section:\n{py}"


def test_javalint_reports_every_java_rule(sections):
    jl = _nows(sections["javalint"])
    assert "Violations.java" in jl, f".java not dispatched to javalint:\n{jl}"
    # JAVA_VIOLATIONS plants one method per rule: swallow (JV001), throwable
    # (JV003), rethrow-without-cause (JV002), useless-catch (JV004), exit (JV005).
    for rule in ("JV001", "JV002", "JV003", "JV004", "JV005"):
        assert rule in jl, f"missing {rule} in javalint section:\n{jl}"


def test_opengrep_reports_go_exit_in_recover(sections):
    og = _nows(sections["erclint-opengrep"])
    assert "go-exit-in-recover" in og, f"missing go-exit-in-recover:\n{og}"


def test_erclint_reports_recover_swallow(sections):
    # GO_EXIT_IN_RECOVER swallows the recovered value -> native ERC007.
    section = sections["erclint"]
    assert "ERC007" in section, f"missing ERC007:\n{section}"
    assert "recover.go" in section, f"ERC007 not attributed to recover.go:\n{section}"


def test_eslint_reports_every_new_ts_rule(sections):
    es = sections["tackbox-eslint"]
    for rule in (
        "ts-rethrow-without-cause",
        "ts-useless-catch",
        "ts-exit-in-catch",
    ):
        assert rule in es, f"missing {rule} in eslint section:\n{es}"


def test_valid_throw_error_no_longer_fires(sections):
    # short `throw new Error("x")` - the removed rule would have flagged it.
    es = sections["tackbox-eslint"]
    assert "valid-throw-error" not in es


def test_no_report_marker_with_reason_suppresses(sections):
    # marker + reason -> suppressed -> file absent from findings.
    py = _nows(sections["pyrules"])
    assert "suppressed_ok.py" not in py, f"marked-with-reason not suppressed:\n{py}"


def test_no_report_marker_without_reason_does_not_suppress(sections):
    # empty reason -> not suppressed -> file present in findings.
    py = _nows(sections["pyrules"])
    assert "marker_no_reason.py" in py, f"empty-reason marker wrongly suppressed:\n{py}"

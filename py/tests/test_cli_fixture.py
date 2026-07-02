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

GO_ERC006_OPENGREP = """package pkg

import "context"

func sentryErr(ctx context.Context, msg string, err error, tags any, key string) {}

func Trigger() {
\tvar tokenErr error
\tsentryErr(context.Background(), "msg", tokenErr, nil, "dedup")
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

FIXTURE_MARKER = "<FIXTURE>"


@pytest.fixture(scope="module")
def fixture_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("goldenrepo")
    (root / "go.mod").write_text(GO_MOD)
    (root / "pkg").mkdir()
    (root / "pkg" / "swallow.go").write_text(GO_ERC001)
    (root / "pkg" / "secret.go").write_text(GO_ERC006_OPENGREP)
    (root / "src").mkdir()
    (root / "src" / "swallow.js").write_text(JS_SWALLOW)
    (root / "docs").mkdir()
    # em-dash (U+2014) triggers no-non-ascii
    (root / "docs" / "notes.md").write_text("# Notes\n\nSome text — dash.\n")

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


def test_all_four_engine_sections_present(fixture_repo):
    result = _run_tackbox(fixture_repo)
    sections = _split_engine_sections(result.stdout)
    assert set(sections) == {
        "erclint",
        "erclint-opengrep",
        "tackbox-eslint",
        "tackbox-mdlint",
    }


def test_erclint_reports_err_swallow_finding(fixture_repo):
    result = _run_tackbox(fixture_repo)
    section = _split_engine_sections(result.stdout)["erclint"]
    assert "errcheck" in section
    assert "ERC001" in section
    assert "swallow.go" in section


def test_opengrep_reports_secret_arg(fixture_repo):
    result = _run_tackbox(fixture_repo)
    section = _split_engine_sections(result.stdout)["erclint-opengrep"]
    assert "erc006" in section
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
        "tackbox-eslint",
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
    Only erclint and opengrep dispatch; opengrep finds nothing on this
    file, so the nonzero aggregate must come from erclint promotion.
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
    assert set(sections) == {"erclint", "erclint-opengrep"}
    assert result.returncode == 1, (
        f"expected 1, got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert "ERC001" in sections["erclint"]

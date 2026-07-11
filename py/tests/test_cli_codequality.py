"""`lint --codequality <path>` writes a CodeClimate report of all findings.

Golden: a two-engine fixture (pyrules natural parse + markdownlint machine
NDJSON) pins the full JSON array - stable order, deterministic fingerprints.
Unit tests pin the branchy format logic (DUP category, location-unknown
pseudo-path); adversarial tests pin the empty-array and unwritable-path
contracts.

Fixture sources are generated inline (not stored under fixtures/) so tackbox
self-lint never encounters them.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import init_repo, tackbox_env

from tackbox.codequality import build_report
from tackbox.engines import Finding

# One swallowed-exception on line 2 (pyrules TBX001) - located via flake8 text.
PY_SWALLOW = """def cleanup():
    try:
        work()
    except ValueError as e:
        pass
"""

# Em-dash (U+2014) on line 3 (markdownlint no-non-ascii) - located via the
# engine's machine NDJSON. Two engines, two parse paths into one report.
MD_NON_ASCII = "# Notes\n\nSome text — dash.\n"

# Golden: sorted by (path, line, rule); fingerprint = sha256("rule:path:line"),
# message excluded; description = "rule: message" carried from the engine.
EXPECTED = [
    {
        "type": "issue",
        "check_name": "MD-ASCII",
        "description": "MD-ASCII: Non-ASCII character [Non-ASCII character U+2014 (—)]",
        "categories": ["Bug Risk"],
        "location": {"path": "docs/notes.md", "lines": {"begin": 3}},
        "fingerprint": "c577a943053a272ef58ecb3aa515e721cb5eca4264535186a8903cfe538e6c0c",
        "severity": "major",
    },
    {
        "type": "issue",
        "check_name": "python-swallowed-exception",
        "description": (
            "python-swallowed-exception: let the exception propagate "
            "or wrap+reraise via raise ... from e"
        ),
        "categories": ["Bug Risk"],
        "location": {"path": "py/swallow.py", "lines": {"begin": 2}},
        "fingerprint": "d44b873ef6b9d98af99e240ec43960a42686882cb1aca0139122b7a0141c1755",
        "severity": "major",
    },
]


def _run(repo: Path, out: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, "-m", "tackbox.cli", "lint", ".",
            "--no-cache", "--codequality", str(out), *extra,
        ],
        cwd=repo,
        env=tackbox_env(),
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def two_engine_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("cq_two_engine")
    (root / "py").mkdir()
    (root / "py" / "swallow.py").write_text(PY_SWALLOW)
    (root / "docs").mkdir()
    (root / "docs" / "notes.md").write_text(MD_NON_ASCII)
    init_repo(root, commit=True)
    return root


# --------- Golden --------------------------------------------------------


def test_codequality_report_matches_golden(two_engine_repo, tmp_path):
    out = tmp_path / "cq.json"
    result = _run(two_engine_repo, out)
    assert out.is_file(), f"report not written; stderr={result.stderr!r}"
    assert json.loads(out.read_text()) == EXPECTED


def test_fingerprints_are_deterministic(two_engine_repo, tmp_path):
    out = tmp_path / "cq.json"
    _run(two_engine_repo, out)
    for issue in json.loads(out.read_text()):
        expect = hashlib.sha256(
            f"{issue['check_name']}:{issue['location']['path']}:"
            f"{issue['location']['lines']['begin']}".encode()
        ).hexdigest()
        assert issue["fingerprint"] == expect


def test_flag_does_not_change_exit_code(two_engine_repo, tmp_path):
    # Findings present -> exit 1 with the report still written (its purpose).
    out = tmp_path / "cq.json"
    result = _run(two_engine_repo, out)
    assert result.returncode == 1
    assert out.is_file()


# --------- Adversarial ---------------------------------------------------


@pytest.fixture(scope="module")
def clean_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("cq_clean")
    (root / "docs").mkdir()
    (root / "docs" / "ok.md").write_text("# Title\n\nPlain ASCII text.\n")
    init_repo(root, commit=True)
    return root


def test_zero_findings_writes_empty_array(clean_repo, tmp_path):
    out = tmp_path / "cq.json"
    result = _run(clean_repo, out)
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert json.loads(out.read_text()) == []


def test_unwritable_path_fails_loudly(two_engine_repo, tmp_path):
    # Parent dir does not exist -> the write fails; the flag must not silently
    # skip. A nonzero exit, not exit 0, is the contract.
    out = tmp_path / "no_such_dir" / "cq.json"
    result = _run(two_engine_repo, out)
    assert result.returncode != 0
    assert not out.exists()
    assert "no_such_dir" in result.stderr


# --------- Unit: report shape --------------------------------------------


def test_build_report_dup_rule_is_duplication_category():
    [issue] = build_report([Finding(rule="DUP001", file="a.go", line=12)])
    assert issue["categories"] == ["Duplication"]
    assert issue["check_name"] == "DUP001"


def test_build_report_non_dup_rule_is_bug_risk():
    [issue] = build_report([Finding(rule="errcheck", file="a.go", line=3)])
    assert issue["categories"] == ["Bug Risk"]


def test_build_report_location_unknown_uses_pseudo_path():
    [issue] = build_report([Finding(rule="opengrep-x", file=None, line=None)])
    assert issue["location"] == {"path": "UNKNOWN", "lines": {"begin": 1}}
    assert issue["fingerprint"] == hashlib.sha256(
        b"opengrep-x:UNKNOWN:1"
    ).hexdigest()


def test_build_report_sorts_by_path_line_rule():
    findings = [
        Finding(rule="rb", file="b.py", line=1),
        Finding(rule="rb", file="a.py", line=9),
        Finding(rule="ra", file="a.py", line=9),
        Finding(rule="ra", file="a.py", line=2),
    ]
    got = [(i["location"]["path"], i["location"]["lines"]["begin"], i["check_name"])
           for i in build_report(findings)]
    assert got == [
        ("a.py", 2, "ra"),
        ("a.py", 9, "ra"),
        ("a.py", 9, "rb"),
        ("b.py", 1, "rb"),
    ]


def test_build_report_empty_is_empty_list():
    assert build_report([]) == []


def test_build_report_description_carries_message():
    [issue] = build_report(
        [Finding(rule="errcheck", file="a.go", line=3, message="must  propagate\n or capture")]
    )
    assert issue["description"] == "errcheck: must propagate or capture"
    assert issue["check_name"] == "errcheck"


def test_build_report_description_bare_rule_without_message():
    [issue] = build_report([Finding(rule="errcheck", file="a.go", line=3)])
    assert issue["description"] == "errcheck"


def test_build_report_fingerprint_ignores_message():
    [bare] = build_report([Finding(rule="errcheck", file="a.go", line=3)])
    [worded] = build_report(
        [Finding(rule="errcheck", file="a.go", line=3, message="reworded diagnostic")]
    )
    assert bare["fingerprint"] == worded["fingerprint"]

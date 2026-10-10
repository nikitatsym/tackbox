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
from test_cli_fixture import REDUNDANT_DUP_MARKER, callable_header_pair

from tackbox import approvals, cli, engines
from tackbox.cli import _approvals_findings
from tackbox.codequality import build_report, engine_findings
from tackbox.engines import Finding

# One swallowed-exception on line 2 (pyrules TBX001) - located via flake8 text.
PY_SWALLOW = """def cleanup():
    try:
        work()
    except ValueError:
        pass
"""

# Em-dash (U+2014) on line 4, under a chars=ascii carrier (declared-chars) -
# located via the engine's machine NDJSON. Two engines, two parse paths into
# one report.
MD_NON_ASCII = "<!-- tackbox: chars=ascii -->\n# Notes\n\nSome text \u2014 dash.\n"

# Golden: sorted by (path, line, rule); fingerprint = sha256("rule:path:line"),
# message excluded; description = "rule: message" carried from the engine.
EXPECTED = [
    {
        "type": "issue",
        "check_name": "MD-CHARS",
        "description": (
            "MD-CHARS: Character outside declared repertoire "
            "[U+2014 (\u2014) is not in the declared character set]"
        ),
        "categories": ["Bug Risk"],
        "location": {"path": "docs/notes.md", "lines": {"begin": 4}},
        "fingerprint": "ffbd277dceddd470ec30804a0eb55d6984db44a0cb6fae91cdf98d4e95432834",
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


# -- Golden


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



@pytest.mark.parametrize("no_cache", [False, True])
def test_broken_go_package_report_preserves_other_package_findings(tmp_path, no_cache):
    module = tmp_path / "nested"
    module.mkdir()
    (module / "go.mod").write_text("module qualityfixture\n\ngo 1.27.0\n")
    (module / "broken").mkdir()
    (module / "broken" / "p.go").write_text("package broken\nfunc Public() { missing() }\n")
    (module / "broken" / "p_test.go").write_text('package broken\nimport "testing"\nfunc TestPublic(t *testing.T) { Public() }\n')
    (module / "clean").mkdir()
    (module / "clean" / "p.go").write_text("package clean\nfunc helper(x int) int { return 1 }\nfunc Public() { helper(1) }\n")
    init_repo(tmp_path, commit=True)
    args = [sys.executable, "-m", "tackbox.cli", "lint", "."]
    if no_cache:
        args.append("--no-cache")
    out = tmp_path / "quality.json"
    plain = subprocess.run(args, cwd=tmp_path, env=tackbox_env(), capture_output=True, text=True)
    result = subprocess.run([*args, "--codequality", str(out)], cwd=tmp_path,
                            env=tackbox_env(), capture_output=True, text=True)
    assert result.returncode == plain.returncode != 0, result.stdout + result.stderr
    issues = json.loads(out.read_text())
    assert len(issues) == 2, issues
    [failure] = [issue for issue in issues if issue["check_name"] == "tackbox-engine-error"]
    assert failure["severity"] == "critical"
    assert failure["location"] == {"path": "nested/broken", "lines": {"begin": 1}}
    assert "erclint" in failure["description"] and "errors in package" in failure["description"]
    assert "\n" not in failure["description"]
    [finding] = [issue for issue in issues if issue["check_name"] == "unusedparams"]
    assert finding["location"] == {"path": "nested/clean/p.go", "lines": {"begin": 2}}
    assert finding["fingerprint"] == hashlib.sha256(b"unusedparams:nested/clean/p.go:2").hexdigest()


@pytest.mark.parametrize("engine,extension,raw,code", [
    ("erclint", ".go", "{", 0),
    ("javalint", ".java", "{", 2),
    ("tackbox-eslint", ".js", "not json", 7),
    ("tackbox-eslint", ".js", "[]", 1),
    ("tackbox-eslint", ".js", '{"rule": [], "line": "bad"}', 1),
    ("pyrules", ".py", "invalid output", 1),
    ("tackbox-eslint", ".js", "", 7),
])
def test_engine_failure_writes_report_without_changing_exit(tmp_path, monkeypatch, engine, extension, raw, code):
    source = "src/a" + extension
    (tmp_path / "src").mkdir()
    (tmp_path / source).write_text("")
    if engine == "erclint":
        (tmp_path / "go.mod").write_text("module fixture\n")
    init_repo(tmp_path, commit=True)
    spec = engines.EngineSpec(engine, frozenset({extension}), lambda *_: ["fixture-engine"],
                              package_mode=engine == "erclint", machine_flag=engine == "tackbox-eslint")
    monkeypatch.setattr(cli, "active_engines", lambda: [spec])
    monkeypatch.setattr(cli, "_print_banner", lambda *_: None)
    real_run = engines.proc.run_bytes

    def run(argv, **kwargs):
        if argv[0] == "fixture-engine":
            return subprocess.CompletedProcess(argv, code, raw.encode(), b"engine failed\nreason")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(engines.proc, "run_bytes", run)
    monkeypatch.chdir(tmp_path)
    plain = cli._run_lint(".", True, False, None)
    report = tmp_path / "quality.json"
    assert cli._run_lint(".", True, False, None, str(report)) == plain != 0
    [issue] = json.loads(report.read_text())
    assert issue["check_name"] == "tackbox-engine-error"
    assert issue["severity"] == "critical"
    path = "." if engine == "erclint" else source
    assert issue["location"] == {"path": path, "lines": {"begin": 1}}
    assert engine in issue["description"] and "\n" not in issue["description"]
    if raw:
        assert "invalid" in issue["description"]


def test_clean_report_has_no_engine_errors(tmp_path):
    (tmp_path / "a.py").write_text("def run():\n    return 1\n")
    init_repo(tmp_path, commit=True)
    out = tmp_path / "quality.json"
    result = _run(tmp_path, out)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(out.read_text()) == []


def test_engine_errors_at_same_scope_have_independent_fingerprints(tmp_path):
    failed = [engines.EngineResult(engine, 2, "", "failed") for engine in ("pyrules", "tackbox-eslint")]
    findings = [finding for result in failed for finding in engine_findings(result, tmp_path)]
    report = build_report(findings)
    assert len({item["fingerprint"] for item in report}) == 2
    assert build_report(list(reversed(findings))) == list(reversed(report))


def test_java_analyzer_errors_share_one_file_issue(tmp_path):
    raw = json.dumps({"src/A.java": {"unused": {"error": "cannot analyze"},
                                    "imports": {"error": "cannot analyze"}}})
    result = engines.normalize_erclint_result(engines.EngineResult("javalint", 2, raw, ""))
    [issue] = build_report(engine_findings(result, tmp_path))
    assert issue["check_name"] == "tackbox-engine-error"
    assert issue["location"] == {"path": "src/A.java", "lines": {"begin": 1}}
    assert "cannot analyze" in issue["description"]


def test_module_crash_does_not_hide_another_modules_findings(tmp_path, monkeypatch):
    for module in ("broken", "clean"):
        (tmp_path / module).mkdir()
        (tmp_path / module / "go.mod").write_text(f"module {module}\n")
    payload = {"clean": {"errcheck": [{"posn": str(tmp_path / "clean/p.go") + ":2:1",
                                      "message": "unchecked error"}]}}

    def run(_argv, *, cwd, **_kwargs):
        if cwd.name == "broken":
            return subprocess.CompletedProcess([], 3, b"", b"crashed\nreason")
        return subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"")

    spec = engines.EngineSpec("erclint", frozenset({".go"}), lambda *_: [], package_mode=True)
    monkeypatch.setattr(engines.proc, "run_bytes", run)
    [result] = engines.run_engines([(spec, ["broken", "clean"])], tmp_path, tmp_path, machine=True)
    issues = build_report(engine_findings(result, tmp_path))
    assert result.exit_code == 3
    assert [(issue["check_name"], issue["location"]["path"]) for issue in issues] == [
        ("tackbox-engine-error", "broken"), ("errcheck", "clean/p.go"),
    ]


# -- approvals findings + fingerprint override seam


def test_fingerprint_override_seam():
    # tackbox-approvals supplies the serialized entry address; every other
    # finding keeps the default sha256(rule:path:line).
    f = Finding("tackbox-approvals", "a.py", 2, "a.py#f: no-report: x")
    [issue] = build_report([f], fingerprint_of=lambda x: "a.py#f")
    assert issue["fingerprint"] == "a.py#f"
    [eng] = build_report([Finding("errcheck", "a.go", 3)])
    assert eng["fingerprint"] == hashlib.sha256(b"errcheck:a.go:3").hexdigest()


def test_codequality_includes_approvals_findings(tmp_path):
    # An uncovered marker rides into --codequality as check_name tackbox-approvals,
    # located at the marker, fingerprinted by the serialized address.
    (tmp_path / "a.py").write_text("def f():\n    return 1  # no-report: uncovered marker\n")
    init_repo(tmp_path, commit=True)
    out = tmp_path / "cq.json"
    _run(tmp_path, out)
    issues = json.loads(out.read_text())
    appr = [i for i in issues if i["check_name"] == "tackbox-approvals"]
    assert len(appr) == 1, issues
    assert appr[0]["location"] == {"path": "a.py", "lines": {"begin": 2}}
    assert appr[0]["fingerprint"] == "a.py#f"  # the address, not sha256(rule:path:line)


def test_approvals_findings_mapping_unit():
    # The mapper: uncovered occurrences at their own lines under the shared
    # address fingerprint; an orphan at its manifest line; an unresolvable
    # file at line 1, fingerprinted by path.
    e = approvals.Entry("a.py#f", "no-report: twice in one scope")
    g = approvals.Entry("gone.py", "no-report: the marker is long gone")
    report = approvals.Report(
        uncovered=[approvals.Uncovered(e, "a.py", 2), approvals.Uncovered(e, "a.py", 3)],
        orphans=[approvals.Orphan(g, 2)],
        unresolvable=["Bad.java"],
    )
    findings, fps = _approvals_findings(report)
    assert [(f.file, f.line) for f in findings] == [
        ("a.py", 2),
        ("a.py", 3),
        (".tackbox/approvals", 2),
        ("Bad.java", 1),
    ]
    assert [fps[f] for f in findings] == ["a.py#f", "a.py#f", "gone.py", "Bad.java"]


def test_codequality_approvals_multiplicity_and_orphan(tmp_path):
    # E2E: the k-th identical occurrence pairs with the k-th entry, so the
    # uncovered tail reports at its own line; the orphan at its manifest line.
    (tmp_path / "a.py").write_text(
        "def f():\n"
        "    x()  # no-report: twice in one scope\n"
        "    y()  # no-report: twice in one scope\n"
    )
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox" / "approvals").write_text(
        "a.py#f: no-report: twice in one scope\n"
        "gone.py: no-report: the marker is long gone\n"
    )
    init_repo(tmp_path, commit=True)
    out = tmp_path / "cq.json"
    _run(tmp_path, out)
    appr = [i for i in json.loads(out.read_text()) if i["check_name"] == "tackbox-approvals"]
    assert [
        (i["location"]["path"], i["location"]["lines"]["begin"], i["fingerprint"])
        for i in appr
    ] == [
        (".tackbox/approvals", 2, "gone.py"),
        ("a.py", 3, "a.py#f"),
    ]


# -- Adversarial


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


# -- Unit: report shape


def test_build_report_dup_rule_is_duplication_category():
    [issue] = build_report([Finding(rule="DUP001", file="a.go", line=12)])
    assert issue["categories"] == ["Duplication"]
    assert issue["check_name"] == "DUP001"


def test_codequality_omits_automatically_filtered_callable_pair(tmp_path):
    (tmp_path / "dup.py").write_text(callable_header_pair())
    init_repo(tmp_path, commit=True)
    out = tmp_path / "cq.json"
    result = _run(tmp_path, out)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not [i for i in json.loads(out.read_text()) if i["check_name"] == "DUP001"]


def test_codequality_reports_redundant_dup_marker_once(tmp_path):
    (tmp_path / "dup.py").write_text(callable_header_pair(REDUNDANT_DUP_MARKER))
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox" / "approvals").write_text(
        "dup.py: dup-ok: shared public callable contract\n"
    )
    init_repo(tmp_path, commit=True)
    out = tmp_path / "cq.json"
    result = _run(tmp_path, out)
    dup003 = [i for i in json.loads(out.read_text()) if i["check_name"] == "DUP003"]
    assert result.returncode == 1, result.stdout + result.stderr
    assert len(dup003) == 1
    assert dup003[0]["categories"] == ["Duplication"]
    assert dup003[0]["location"] == {"path": "dup.py", "lines": {"begin": 1}}


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


def test_repeated_same_location_findings_have_distinct_stable_fingerprints():
    findings = [Finding("F401", "a.py", 1, "unused os"), Finding("F401", "a.py", 1, "unused sys")]
    report = build_report(findings)
    assert report[0]["fingerprint"] == hashlib.sha256(b"F401:a.py:1").hexdigest()
    assert len({issue["fingerprint"] for issue in report}) == 2
    reworded = build_report([Finding("F401", "a.py", 1, "changed"), Finding("F401", "a.py", 1, "again")])
    assert [i["fingerprint"] for i in reworded] == [i["fingerprint"] for i in report]
    unrelated = Finding("F841", "z.py", 2, "unused local")
    assert build_report([unrelated, *findings])[:2] == report
    assert {i["fingerprint"] for i in build_report([*reversed(findings), unrelated])[:2]} == {
        i["fingerprint"] for i in report
    }


def test_approved_suppression_is_invisible_to_lint_and_codequality(tmp_path):
    (tmp_path / "dev.py").write_text("# hook entry\n")
    (tmp_path / "app.js").write_text(
        "// no-report: caller tolerates this failure\ntry { work() } catch {}\n"
    )
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox/approvals").write_text("app.js: no-report: caller tolerates this failure\n")
    init_repo(tmp_path, commit=True)
    report = tmp_path / "quality.json"
    result = _run(tmp_path, report)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "every catch path" not in result.stdout
    assert json.loads(report.read_text()) == []

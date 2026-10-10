"""Unused Python bindings in engine, CLI, and hook output."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tackbox.engines import DEV_ENGINES, located_findings
from tackbox.pyrules.flake8_runner import run as run_flake8

ROOT = Path(__file__).resolve().parents[2]


def _engine(tmp_path, source, machine=False):
    (tmp_path / "sample.py").write_text(source)
    engine = next(e for e in DEV_ENGINES if e.id == "pyrules")
    argv = engine.build_argv(tmp_path, ROOT, ["sample.py"], (), tmp_path)
    if machine:
        argv.append("--machine")
    result = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True)
    return result, located_findings("pyrules", result.stdout, tmp_path)


def test_unused_python_bindings_reach_both_output_forms(tmp_path):
    source = "import os  # noqa: F401\ndef run():\n    unused = 1\n    return 2\n"
    (tmp_path / "setup.cfg").write_text("[flake8]\nignore = F401,F841\n")
    for machine in (False, True):
        result, findings = _engine(tmp_path, source, machine)
        assert result.returncode == 1, result.stderr
        assert {(f.rule, f.line) for f in findings} == {
            ("python-unused-import", 1), ("python-unused-variable", 3),
        }


def test_private_local_names_are_not_unused_exemptions(tmp_path):
    source = "def run():\n    _reserved = 1\n    _ = 2\n    return 3\n"
    for machine in (False, True):
        result, findings = _engine(tmp_path, source, machine)
        assert result.returncode == 1, result.stdout + result.stderr
        assert [(f.rule, f.line) for f in findings] == [("python-unused-variable", 2)]


def test_private_names_reach_both_output_forms_without_markers(tmp_path):
    source = "# no-report: not an unused-code escape\ndef _dead():\n    return _dead()\n"
    for machine in (False, True):
        result, findings = _engine(tmp_path, source, machine)
        assert result.returncode == 1, result.stdout + result.stderr
        assert [(f.rule, f.line) for f in findings] == [("python-unused-private", 2)]
        assert findings[0].message.startswith('`_dead` is not used in this module;')


def test_private_all_exports_do_not_change_f401_string_credit(tmp_path):
    source = "from m import private\n_dead = 1\ngetattr(other, 'private')\n__all__ = ['_dead']\n"
    result, findings = _engine(tmp_path, source, True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert [(f.rule, f.line) for f in findings] == [("python-unused-import", 1)]
    for exports in ("['private']", "('private',)", "['public'] + ['private']"):
        result, findings = _engine(tmp_path, "from m import private\n__all__ = " + exports + "\n", True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert findings == []


def test_private_import_spelling_alone_is_not_side_effect_credit(tmp_path):
    source = "import _module\nfrom m import _private\n"
    result, findings = _engine(tmp_path, source, True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert [(f.rule, f.line) for f in findings] == [
        ("python-unused-import", 1), ("python-unused-import", 2),
    ]



def test_from_import_alias_has_no_side_effect_exemption(tmp_path):
    for machine in (False, True):
        result, findings = _engine(tmp_path, "from json import dumps as _dumps\n", machine)
        assert result.returncode == 1, result.stdout + result.stderr
        assert [(f.rule, f.line) for f in findings] == [("python-unused-import", 1)]


def test_formatter_returns_only_emitted_findings(tmp_path, capsys):
    target = tmp_path / 'sample.py'
    target.write_text("import os as _os, sys\nfrom json import dumps as _dumps\n")
    count = run_flake8([str(target)], ['--isolated', '--disable-noqa', '--select=F401'], True)
    assert count == 2
    findings = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(findings) == 2
    assert all(item['rule'] == 'python-unused-import' for item in findings)

def test_underscore_bindings_and_all_reexports_are_clean(tmp_path):
    source = """import os as _os
from pathlib import Path
__all__ = ['Path']
def run():
    _ = 1
    _ = 2
    return 3
"""
    source += "import xml.etree as _etree\n"
    for machine in (False, True):
        result, findings = _engine(tmp_path, source, machine)
        assert result.returncode == 0, result.stdout + result.stderr
        assert findings == []


def test_import_conventions_do_not_hide_other_bindings(tmp_path):
    source = """import os as _os, sys
from pathlib import Path as Path
from os import path as _path, environ
"""
    result, findings = _engine(tmp_path, source, True)
    assert result.returncode == 1, result.stderr
    assert len(findings) == 4
    assert all(f.rule == "python-unused-import" for f in findings)
    assert {f.line for f in findings} == {1, 2, 3}


def test_unselected_pyflakes_rules_do_not_expand_scope(tmp_path):
    result, findings = _engine(tmp_path, "def run():\n    return undefined_name\n", True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert findings == []


def test_cli_codequality_preserves_unused_rule_ids(tmp_path):
    (tmp_path / "sample.py").write_text("import os\ndef run():\n    unused = 1\n    return 2\ndef _dead():\n    pass\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    report = tmp_path / "quality.json"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "py")}
    result = subprocess.run([
        sys.executable, "-m", "tackbox.cli", "lint", ".", "--no-cache",
        "--codequality", str(report),
    ], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    ids = {entry["check_name"] for entry in json.loads(report.read_text())}
    assert {"python-unused-import", "python-unused-variable", "python-unused-private"} <= ids
    assert "python-unused-import" in result.stdout
    assert "python-unused-variable" in result.stdout
    assert "python-unused-private" in result.stdout

def test_hook_reports_transient_import_without_undoing_the_edit(tmp_path):
    (tmp_path / "dev.py").write_text("")
    target = tmp_path / "sample.py"
    target.write_text("def run():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "py")}

    def post(fragment):
        event = {
            "hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(tmp_path),
            "tool_input": {"file_path": str(target), "old_string": "", "new_string": fragment},
        }
        return subprocess.run([
            sys.executable, "-m", "tackbox.cli", "hook",
        ], input=json.dumps(event), cwd=tmp_path, env=env, capture_output=True, text=True)

    target.write_text("import os\ndef run():\n    return 1\n")
    first = post("import os\n")
    assert first.returncode == 2, first.stdout + first.stderr
    assert "python-unused-import" in first.stderr
    assert target.read_text().startswith("import os\n")
    target.write_text("import os\ndef run():\n    return os.name\n")
    second = post("    return os.name\n")
    assert second.returncode == 0, second.stdout + second.stderr

def test_underscore_convention_survives_multi_file_flake8_runs(tmp_path):
    files = []
    for index in range(8):
        name = f"sample{index}.py"
        (tmp_path / name).write_text("import os as _os\ndef run():\n    _ = 1\n    return 2\n")
        files.append(name)
    engine = next(e for e in DEV_ENGINES if e.id == "pyrules")
    for machine in (False, True):
        argv = engine.build_argv(tmp_path, ROOT, files, (), tmp_path)
        if machine:
            argv.append("--machine")
        result = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert located_findings("pyrules", result.stdout, tmp_path) == []


def test_hook_reports_private_declaration_on_whole_file_write(tmp_path):
    (tmp_path / "dev.py").write_text("")
    target = tmp_path / "sample.py"
    target.write_text("def _dead():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    event = {
        "hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": str(tmp_path),
        "tool_input": {"file_path": str(target), "content": target.read_text()},
    }
    result = subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "hook"],
        input=json.dumps(event), cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "py")},
        capture_output=True, text=True,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "python-unused-private" in result.stderr
    assert target.read_text() == "def _dead():\n    return 1\n"

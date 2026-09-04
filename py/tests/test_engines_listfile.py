"""Long-argv safety: every engine hands its file/package list to the child through
a list-file (go/node: --paths-from/--files-from) or a JDK @argfile (javalint),
never as thousands of positional argv entries.

The unit tests pin the argv shape plus the list-file content (order preserved);
the adversarial test proves a 1 MiB list spawns clean without becoming positional
child-process argv.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import tackbox.engines as engines
from tackbox.engines import DEV_ENGINES, HERMETIC_ENGINES


def _dev(id_):
    return next(e for e in DEV_ENGINES if e.id == id_)


def _herm(id_):
    return next(e for e in HERMETIC_ENGINES if e.id == id_)


def _lines(path: str) -> list[str]:
    return Path(path).read_text(encoding="utf-8").splitlines()

# Every @argfile token is quoted: '#' starts a comment in the JDK argfile
# grammar, and an unquoted space would split one path into two arguments.
def _java_argfile_token(value: str | Path) -> str:
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'



def _after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _stub_go_binary(monkeypatch):
    monkeypatch.setattr(
        engines, "_built_go_binary", lambda root, name: Path("/fake/bin") / name
    )


def _write_empty_jscpd_stub(path: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        'out=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--output" ]; then out="$2"; shift 2; else shift; fi\n'
        "done\n"
        "printf '%s' '{\"duplicates\":[]}' > \"$out/jscpd-report.json\"\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


# -- go binaries: --paths-from list-file -----------------------------------


def test_dev_erclint_argv_uses_paths_from_not_positional(monkeypatch, tmp_path):
    _stub_go_binary(monkeypatch)
    argv = _dev("erclint").build_argv(
        Path("/repo"), Path("/tb"), ["a/pkg", "b/pkg"], (), tmp_path
    )
    assert "--paths-from" in argv
    # packages ride the list file (as ./pkg patterns), never positional argv.
    assert not any(a.startswith("./") for a in argv)
    assert _lines(_after(argv, "--paths-from")) == ["./a/pkg", "./b/pkg"]


def test_dev_opengrep_argv_uses_paths_from_not_positional(monkeypatch, tmp_path):
    _stub_go_binary(monkeypatch)
    argv = _dev("erclint-opengrep").build_argv(
        Path("/repo"), Path("/tb"), ["x.go", "y.go"], (), tmp_path
    )
    assert "--paths-from" in argv
    assert "x.go" not in argv and "y.go" not in argv
    assert _lines(_after(argv, "--paths-from")) == ["x.go", "y.go"]


@pytest.mark.skipif(
    os.name == "nt", reason="jscpd test stubs require POSIX shell execution"
)
def test_dev_jscpd_argv_uses_paths_from_not_positional(monkeypatch, tmp_path):
    _stub_go_binary(monkeypatch)
    jscpd = tmp_path / "jscpd"
    _write_empty_jscpd_stub(jscpd)
    monkeypatch.setattr(engines, "_dev_jscpd_bin", lambda root: jscpd)
    repo = tmp_path / "repo"
    repo.mkdir()
    argv = _dev("tackbox-jscpd").build_argv(
        repo, Path("/tb"), ["x.go", "z.java"], (), tmp_path
    )
    assert argv[:2] == [str(Path("/fake/bin/tackbox-jscpd")), "--report"]
    assert "--callable-zones" not in argv
    assert "--paths-from" in argv
    assert "x.go" not in argv and "z.java" not in argv
    assert _lines(_after(argv, "--paths-from")) == ["x.go", "z.java"]


def test_hermetic_opengrep_argv_uses_paths_from(tmp_path):
    argv = _herm("erclint-opengrep").build_argv(
        Path("/repo"), Path("/tb"), ["x.go"], (), tmp_path
    )
    assert "--paths-from" in argv
    assert "x.go" not in argv
    assert _lines(_after(argv, "--paths-from")) == ["x.go"]


@pytest.mark.skipif(
    os.name == "nt", reason="jscpd test stubs require POSIX shell execution"
)
def test_hermetic_jscpd_argv_uses_paths_from(monkeypatch, tmp_path):
    # env override makes hermetic_engines_root() resolve without engines.json.
    store = tmp_path / "store"
    (store / "bin").mkdir(parents=True)
    _write_empty_jscpd_stub(store / "bin" / engines.exe_name("jscpd"))
    monkeypatch.setenv(engines.ENGINES_DIR_ENV, str(store))
    repo = tmp_path / "repo"
    repo.mkdir()
    argv = _herm("tackbox-jscpd").build_argv(
        repo, Path("/tb"), ["x.go"], (), tmp_path
    )
    assert argv[:2] == [
        str(engines._hermetic_erclint_bin("tackbox-jscpd")),
        "--report",
    ]
    assert "--paths-from" in argv
    assert "x.go" not in argv
    assert _lines(_after(argv, "--paths-from")) == ["x.go"]


def _fake_jscpd_run(
    report: dict, calls: list[list[str]], configs: list[dict]
):
    def run(argv, *, cwd, stdout, stderr):
        calls.append(argv)
        config = Path(argv[argv.index("--config") + 1])
        configs.append(json.loads(config.read_text(encoding="utf-8")))
        output = Path(argv[argv.index("--output") + 1])
        (output / "jscpd-report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    return run


def _endpoint(name: str) -> dict:
    return {
        "name": name,
        "startLoc": {"line": 1, "column": 0},
        "endLoc": {"line": 1, "column": 5},
    }


def test_jscpd_preparation_runs_once_and_zero_clone_skips_ast(
    monkeypatch, tmp_path
):
    calls: list[list[str]] = []
    configs: list[dict] = []
    monkeypatch.setattr(
        engines.subprocess,
        "run",
        _fake_jscpd_run({"duplicates": []}, calls, configs),
    )

    def unexpected(*_args):
        raise AssertionError("zero-clone report must not invoke ast-grep")

    monkeypatch.setattr(engines.callable_zones, "zones_for_file", unexpected)
    argv = engines._prepare_jscpd_argv(
        Path("/wrapper"),
        Path("/jscpd"),
        tmp_path,
        ["a.py", "b.go"],
        tmp_path,
    )
    assert len(calls) == 1
    assert configs == [
        {"path": [str(tmp_path / "a.py"), str(tmp_path / "b.go")]}
    ]
    assert "--callable-zones" not in argv


def test_jscpd_preparation_parses_only_unique_physical_endpoint_files(
    monkeypatch, tmp_path
):
    a = tmp_path / "a.py"
    b = tmp_path / "B.svelte"
    a.write_text("def a():\n    pass\n", encoding="utf-8")
    b.write_text("<script>const b = () => 1;</script>\n", encoding="utf-8")
    report = {
        "duplicates": [
            {
                "firstFile": _endpoint(str(a)),
                "secondFile": _endpoint(str(b) + ":script"),
            },
            {
                "firstFile": _endpoint(str(a)),
                "secondFile": _endpoint(str(b) + ":script"),
            },
        ]
    }
    calls: list[list[str]] = []
    configs: list[dict] = []
    monkeypatch.setattr(
        engines.subprocess, "run", _fake_jscpd_run(report, calls, configs)
    )
    parsed: list[str] = []

    def zones(_root, rel):
        parsed.append(rel)
        return [
            engines.callable_zones.Zone(
                engines.callable_zones.Point(0, 0),
                engines.callable_zones.Point(0, 10),
            )
        ]

    monkeypatch.setattr(engines.callable_zones, "zones_for_file", zones)
    argv = engines._prepare_jscpd_argv(
        Path("/wrapper"), Path("/jscpd"), tmp_path, ["a.py", "B.svelte"], tmp_path
    )
    assert len(calls) == 1
    assert parsed == ["B.svelte", "a.py"]
    sidecar = json.loads(
        Path(_after(argv, "--callable-zones")).read_text(encoding="utf-8")
    )
    assert list(sidecar["files"]) == ["B.svelte", "a.py"]
    assert sidecar["files"]["a.py"] == [
        {
            "start": {"line": 0, "column": 0},
            "end": {"line": 0, "column": 10},
        }
    ]


def test_jscpd_endpoint_extended_length_name_maps_to_repo_relative(tmp_path):
    if os.name != "nt":
        pytest.skip("extended-length prefixes are a windows path spelling")
    a = tmp_path / "sub" / "a.py"
    a.parent.mkdir()
    a.write_text("def a():\n    pass\n", encoding="utf-8")
    physical = engines._physical_endpoint_path(tmp_path, "\\\\?\\" + str(a.resolve()))
    assert physical is not None
    assert physical[1] == "sub/a.py"


def test_jscpd_preparation_malformed_report_is_loud(monkeypatch, tmp_path):
    def malformed(argv, *, cwd, stdout, stderr):
        output = Path(argv[argv.index("--output") + 1])
        (output / "jscpd-report.json").write_text("{", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(engines.subprocess, "run", malformed)
    with pytest.raises(engines.EnginesStoreError, match="parse jscpd report"):
        engines._prepare_jscpd_argv(
            Path("/wrapper"), Path("/jscpd"), tmp_path, ["a.py"], tmp_path
        )


def test_jscpd_preparation_nonzero_detector_exit_is_loud(monkeypatch, tmp_path):
    def fail(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["jscpd"], returncode=2, stdout=b"", stderr=b"bad config"
        )

    monkeypatch.setattr(engines.subprocess, "run", fail)
    with pytest.raises(engines.EnginesStoreError, match=r"jscpd.*failed \(2\).*bad config"):
        engines._prepare_jscpd_argv(
            Path("/wrapper"), Path("/jscpd"), tmp_path, ["a.py"], tmp_path
        )


def test_jscpd_preparation_ast_failure_is_loud(monkeypatch, tmp_path):
    source = tmp_path / "a.py"
    source.write_text("def a():\n    pass\n", encoding="utf-8")
    report = {
        "duplicates": [
            {"firstFile": _endpoint(str(source)), "secondFile": _endpoint(str(source))}
        ]
    }
    monkeypatch.setattr(
        engines.subprocess, "run", _fake_jscpd_run(report, [], [])
    )

    def fail(_root, _rel):
        raise RuntimeError("ast-grep failed")

    monkeypatch.setattr(engines.callable_zones, "zones_for_file", fail)
    with pytest.raises(RuntimeError, match="ast-grep failed"):
        engines._prepare_jscpd_argv(
            Path("/wrapper"), Path("/jscpd"), tmp_path, ["a.py"], tmp_path
        )


def test_jscpd_preparation_unmappable_endpoints_get_explicit_empty_sidecar(
    monkeypatch, tmp_path
):
    endpoint = _endpoint(str(tmp_path / "missing.py"))
    report = {
        "duplicates": [
            {"firstFile": endpoint, "secondFile": endpoint}
        ]
    }
    monkeypatch.setattr(
        engines.subprocess, "run", _fake_jscpd_run(report, [], [])
    )
    argv = engines._prepare_jscpd_argv(
        Path("/wrapper"), Path("/jscpd"), tmp_path, ["missing.py"], tmp_path
    )
    assert json.loads(
        Path(_after(argv, "--callable-zones")).read_text(encoding="utf-8")
    ) == {"files": {}}


def test_jscpd_preparation_huge_file_set_stays_off_inner_argv(
    monkeypatch, tmp_path
):
    argv_budget = 1 << 20
    template = f"src/{'d' * 180}/file_{{i:06d}}.go"
    per_path = len(template.format(i=0)) + 1
    paths = [template.format(i=i) for i in range(argv_budget // per_path + 512)]
    assert sum(len(path.encode()) + 1 for path in paths) > argv_budget

    calls: list[list[str]] = []
    configs: list[dict] = []
    monkeypatch.setattr(
        engines.subprocess,
        "run",
        _fake_jscpd_run({"duplicates": []}, calls, configs),
    )
    argv = engines._prepare_jscpd_argv(
        Path("/wrapper"), Path("/jscpd"), tmp_path, paths, tmp_path
    )
    assert len(calls) == 1
    assert len(configs[0]["path"]) == len(paths)
    assert sum(len(token.encode()) + 1 for token in calls[0]) < 4096
    assert sum(len(token.encode()) + 1 for token in argv) < 4096
    assert len(_lines(_after(argv, "--paths-from"))) == len(paths)


# -- node binaries: --files-from list-file ---------------------------------


def test_dev_eslint_argv_uses_files_from_not_positional(tmp_path):
    argv = _dev("tackbox-eslint").build_argv(
        Path("/repo"), Path("/tb"), ["a.js", "b.ts"], (), tmp_path
    )
    assert argv[:2] == ["node", str(Path("/tb") / "bin" / "tackbox-eslint.js")]
    assert "--files-from" in argv
    assert "a.js" not in argv and "b.ts" not in argv
    assert _lines(_after(argv, "--files-from")) == ["a.js", "b.ts"]


def test_dev_mdlint_argv_uses_files_from_not_positional(tmp_path):
    argv = _dev("tackbox-mdlint").build_argv(
        Path("/repo"), Path("/tb"), ["a.md", "b.md"], (), tmp_path
    )
    assert "--files-from" in argv
    assert "a.md" not in argv and "b.md" not in argv
    assert _lines(_after(argv, "--files-from")) == ["a.md", "b.md"]


def test_hermetic_eslint_argv_uses_files_from(monkeypatch, tmp_path):
    monkeypatch.setenv(engines.ENGINES_DIR_ENV, str(tmp_path / "store"))
    argv = _herm("tackbox-eslint").build_argv(
        Path("/repo"), Path("/tb"), ["a.js"], (), tmp_path
    )
    assert "--files-from" in argv
    assert "a.js" not in argv
    assert _lines(_after(argv, "--files-from")) == ["a.js"]


def test_hermetic_mdlint_argv_uses_files_from(monkeypatch, tmp_path):
    monkeypatch.setenv(engines.ENGINES_DIR_ENV, str(tmp_path / "store"))
    argv = _herm("tackbox-mdlint").build_argv(
        Path("/repo"), Path("/tb"), ["a.md"], (), tmp_path
    )
    assert "--files-from" in argv
    assert "a.md" not in argv
    assert _lines(_after(argv, "--files-from")) == ["a.md"]


# -- javalint: JDK @argfile ------------------------------------------------


def test_dev_javalint_argv_uses_argfile_not_positional(monkeypatch, tmp_path):
    jar = Path("/fake/javalint.jar")
    monkeypatch.setattr(engines, "_built_javalint_jar", lambda root: jar)
    argv = _dev("javalint").build_argv(
        Path("/repo"), Path("/tb"), ["A.java", "B.java"], (), tmp_path
    )
    assert argv[0] == "java"
    assert len(argv) == 2 and argv[1].startswith("@")
    assert "A.java" not in argv and "B.java" not in argv
    body = _lines(argv[1][1:])
    assert body == [
        _java_argfile_token("-jar"),
        _java_argfile_token(jar),
        _java_argfile_token("A.java"),
        _java_argfile_token("B.java"),
    ]


def test_hermetic_javalint_argfile_quotes_reporters_and_paths(tmp_path):
    jar = engines._TACKBOX_PKG_ROOT / "bin" / "javalint.jar"
    argv = _herm("javalint").build_argv(
        Path("/repo"),
        Path("/tb"),
        ["dir with space/C.java"],
        (("Rep.java", "Rep.report", "capture"),),
        tmp_path,
    )
    assert argv[0] == "java" and argv[1].startswith("@")
    body = _lines(argv[1][1:])
    assert body == [
        _java_argfile_token("-jar"),
        _java_argfile_token(jar),
        _java_argfile_token("--reporters=Rep.java#Rep.report"),
        _java_argfile_token("dir with space/C.java"),
    ]


def test_java_argfile_escapes_backslash_and_quote(tmp_path):
    name = engines._write_java_argfile(tmp_path, ['a\\b', 'c"d'])
    assert Path(name).read_text(encoding="utf-8") == '"a\\\\b"\n"c\\"d"\n'


def test_list_files_written_with_lf_only(tmp_path):
    # Text mode without newline= would emit CRLF on Windows; a trailing `\r` on a
    # path breaks every list-file reader ("File not found: a.go\r").
    paths = engines._write_paths_file(tmp_path, ["a.go", "b/c.py"])
    argfile = engines._write_java_argfile(tmp_path, ["-jar", "x.jar", "A.java"])
    linktargets = engines._write_link_targets_file(tmp_path, (("F", "a.md"), ("L", "s.md")))
    assert b"\r" not in Path(paths).read_bytes()
    assert b"\r" not in Path(argfile).read_bytes()
    assert b"\r" not in Path(linktargets).read_bytes()


# -- tackbox-mdlint: the mandatory link-target flags (D018) ----------------


def test_link_targets_file_is_tab_separated_kind_path(tmp_path):
    name = engines._write_link_targets_file(
        tmp_path, (("F", "a.md"), ("L", "link.md"), ("G", "vendor/sub"))
    )
    assert _lines(name) == ["F\ta.md", "L\tlink.md", "G\tvendor/sub"]


def _mdlint_run(engine, tmp_path):
    return engines.EngineRun(
        engine=engine,
        args=["a.md"],
        repo_root=Path("/repo"),
        tackbox_root=Path("/tb"),
        link_targets=(("F", "a.md"), ("F", "docs/b.md")),
    )


def test_dev_mdlint_run_appends_repo_root_and_link_targets(tmp_path):
    run = _mdlint_run(_dev("tackbox-mdlint"), None)
    argv = engines._link_target_argv(run, ["node", "wrapper", "--files-from", "L"], tmp_path)
    assert argv[:4] == ["node", "wrapper", "--files-from", "L"]
    assert argv[argv.index("--repo-root") + 1] == str(Path("/repo"))
    assert "--link-targets-from" in argv


def test_dev_mdlint_run_writes_the_inventory_the_wrapper_reads(tmp_path):
    run = _mdlint_run(_dev("tackbox-mdlint"), None)
    argv = engines._link_target_argv(run, ["node", "wrapper"], tmp_path)
    assert _lines(_after(argv, "--link-targets-from")) == ["F\ta.md", "F\tdocs/b.md"]


def test_non_mdlint_engine_gets_no_link_target_flags(tmp_path):
    run = engines.EngineRun(
        engine=_dev("tackbox-eslint"),
        args=["a.js"],
        repo_root=Path("/repo"),
        tackbox_root=Path("/tb"),
        link_targets=(("F", "a.md"),),
    )
    argv = engines._link_target_argv(run, ["node", "eslint"], tmp_path)
    assert argv == ["node", "eslint"]


# -- pyrules: --files-from on our checker CLI ------------------------------


def test_pyrules_argv_uses_checker_files_from_not_flake8(tmp_path):
    argv = _dev("pyrules").build_argv(
        Path("/repo"), Path("/tb"), ["a.py", "b.py"], (), tmp_path
    )
    assert argv[0] == sys.executable
    assert _after(argv, "-m") == "tackbox.pyrules.checker"
    assert "--files-from" in argv
    assert "--isolated" in argv and "--disable-noqa" in argv and "--select=TBX" in argv
    assert "a.py" not in argv and "b.py" not in argv
    assert _lines(_after(argv, "--files-from")) == ["a.py", "b.py"]


_PY_SWALLOW = (
    "def h():\n    try:\n        work()\n    except ValueError as e:\n        pass\n"
)


def _run_pyrules_checker(tmp_path, name: str, body: str) -> subprocess.CompletedProcess:
    (tmp_path / name).write_text(body, encoding="utf-8")
    listf = tmp_path / "list.txt"
    listf.write_text(name + "\n", encoding="utf-8")
    return subprocess.run(
        [
            sys.executable, "-m", "tackbox.pyrules.checker",
            "--files-from", str(listf),
            "--isolated", "--disable-noqa", "--select=TBX",
        ],
        cwd=tmp_path, capture_output=True, text=True,
    )


def test_pyrules_checker_cli_reads_files_from_and_flags_swallow(tmp_path):
    r = _run_pyrules_checker(tmp_path, "bad.py", _PY_SWALLOW)
    assert r.returncode == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "TBX001" in r.stdout, r.stdout


def test_pyrules_checker_cli_clean_file_exits_zero(tmp_path):
    r = _run_pyrules_checker(tmp_path, "ok.py", "def f():\n    return 1\n")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout == ""


# -- adversarial: an oversized list spawns clean where raw argv would E2BIG -


def _stub_exit0(path: Path) -> Path:
    if os.name == "nt":
        path = path.with_suffix(".cmd")
        path.write_text(
            "@echo off\r\n"
            "setlocal EnableDelayedExpansion\r\n"
            "set /a count=0\r\n"
            'for /f "usebackq delims=" %%L in ("%2") do set /a count+=1\r\n'
            "echo !count!\r\n"
            "exit /b 0\r\n",
            encoding="utf-8",
        )
        return path
    path.write_text(
        "#!/bin/sh\n"
        'wc -l < "$2"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_listfile_spawn_survives_a_path_list_larger_than_product_argv_budget(
    monkeypatch, tmp_path
):
    # stress_bytes exceeds both platform branches of the Go wrapper's argv budget.
    stress_bytes = 1 << 20
    template = f"src/{'d' * 180}/file_{{i:06d}}.go"
    per_path = len(template.format(i=0)) + 1
    paths = [template.format(i=i) for i in range(stress_bytes // per_path + 512)]
    raw_bytes = sum(len(path.encode("utf-8")) + 1 for path in paths)
    assert raw_bytes > stress_bytes, raw_bytes

    stub = _stub_exit0(tmp_path / "stub-engine")
    monkeypatch.setattr(engines, "_built_go_binary", lambda root, name: stub)

    argv = _dev("erclint-opengrep").build_argv(
        Path("/repo"), Path("/tb"), paths, (), tmp_path
    )
    # Only the list-file reference reaches the child process.
    spawn_bytes = sum(len(a.encode("utf-8")) + 1 for a in argv)
    assert spawn_bytes < 4096, spawn_bytes

    completed = subprocess.run(argv, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert int(completed.stdout.strip()) == len(paths)

"""ARG_MAX safety: every engine hands its file/package list to the child through
a list-file (go/node: --paths-from/--files-from) or a JDK @argfile (javalint),
never as thousands of positional argv entries.

The unit tests pin the argv shape plus the list-file content (order preserved);
the adversarial test proves a >1 MB list spawns clean through the new mechanism,
where the same list as raw argv would exceed ARG_MAX (E2BIG).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import tackbox.engines as engines
from tackbox.engines import DEV_ENGINES, HERMETIC_ENGINES


def _dev(id_):
    return next(e for e in DEV_ENGINES if e.id == id_)


def _herm(id_):
    return next(e for e in HERMETIC_ENGINES if e.id == id_)


def _lines(path: str) -> list[str]:
    return Path(path).read_text(encoding="utf-8").splitlines()


def _after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _stub_go_binary(monkeypatch):
    monkeypatch.setattr(
        engines, "_built_go_binary", lambda root, name: Path("/fake/bin") / name
    )


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


def test_dev_jscpd_argv_uses_paths_from_not_positional(monkeypatch, tmp_path):
    _stub_go_binary(monkeypatch)
    monkeypatch.setattr(engines, "_dev_jscpd_bin", lambda root: Path("/fake/jscpd"))
    argv = _dev("tackbox-jscpd").build_argv(
        Path("/repo"), Path("/tb"), ["x.go", "z.java"], (), tmp_path
    )
    assert argv[:3] == [str(Path("/fake/bin/tackbox-jscpd")), "--jscpd", "/fake/jscpd"]
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


def test_hermetic_jscpd_argv_uses_paths_from(monkeypatch, tmp_path):
    # env override makes hermetic_engines_root() resolve without engines.json.
    monkeypatch.setenv(engines.ENGINES_DIR_ENV, str(tmp_path / "store"))
    argv = _herm("tackbox-jscpd").build_argv(
        Path("/repo"), Path("/tb"), ["x.go"], (), tmp_path
    )
    assert "--paths-from" in argv
    assert "x.go" not in argv
    assert _lines(_after(argv, "--paths-from")) == ["x.go"]


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
    monkeypatch.setattr(
        engines, "_built_javalint_jar", lambda root: Path("/fake/javalint.jar")
    )
    argv = _dev("javalint").build_argv(
        Path("/repo"), Path("/tb"), ["A.java", "B.java"], (), tmp_path
    )
    assert argv[0] == "java"
    assert len(argv) == 2 and argv[1].startswith("@")
    assert "A.java" not in argv and "B.java" not in argv
    # The whole invocation rides the argfile (expansion only happens in the
    # launcher-options slot): -jar, jar path, then the files, each a quoted token.
    body = _lines(argv[1][1:])
    assert body == ['"-jar"', '"/fake/javalint.jar"', '"A.java"', '"B.java"']


def test_hermetic_javalint_argfile_quotes_reporters_and_paths(tmp_path):
    jar = str(engines._TACKBOX_PKG_ROOT / "bin" / "javalint.jar")
    argv = _herm("javalint").build_argv(
        Path("/repo"),
        Path("/tb"),
        ["dir with space/C.java"],
        (("Rep.java", "Rep.report", "capture"),),
        tmp_path,
    )
    assert argv[0] == "java" and argv[1].startswith("@")
    body = _lines(argv[1][1:])
    # '#' is the argfile comment char, so the reporters flag must be quoted; a
    # space in a path must survive too.
    assert body == [
        '"-jar"',
        f'"{jar}"',
        '"--reporters=Rep.java#Rep.report"',
        '"dir with space/C.java"',
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


def test_dev_mdlint_run_appends_repo_root_and_link_targets():
    run = _mdlint_run(_dev("tackbox-mdlint"), None)
    argv = engines._link_target_argv(run, ["node", "wrapper", "--files-from", "L"], Path("/tmp"))
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


# -- adversarial: >1 MB list spawns clean where raw argv would E2BIG -------


def _stub_exit0(path: Path) -> None:
    path.write_text(
        "#!/bin/sh\n# argv: <bin> --paths-from <listfile>; count the lines it holds\n"
        'wc -l < "$2"\nexit 0\n',
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_listfile_spawn_survives_a_path_list_that_would_exceed_arg_max(
    monkeypatch, tmp_path
):
    # Enough long paths to exceed the PLATFORM's ARG_MAX (1 MiB on macOS, ~2 MiB
    # on Linux runners - a fixed count is not adversarial everywhere); passed as
    # raw argv this is the E2BIG crash the fix removes.
    arg_max = os.sysconf("SC_ARG_MAX")
    template = f"src/{'d' * 180}/file_{{i:06d}}.go"
    per_path = len(template.format(i=0)) + 1
    paths = [template.format(i=i) for i in range(arg_max // per_path + 512)]
    raw_bytes = sum(len(p.encode("utf-8")) + 1 for p in paths)
    assert raw_bytes > arg_max, raw_bytes

    stub = tmp_path / "stub-engine"
    _stub_exit0(stub)
    monkeypatch.setattr(engines, "_built_go_binary", lambda root, name: stub)

    argv = _dev("erclint-opengrep").build_argv(
        Path("/repo"), Path("/tb"), paths, (), tmp_path
    )
    # The spawned argv itself is tiny - the list never touches the boundary.
    spawn_bytes = sum(len(a.encode("utf-8")) + 1 for a in argv)
    assert spawn_bytes < 4096, spawn_bytes

    completed = subprocess.run(argv, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert int(completed.stdout.strip()) == len(paths)

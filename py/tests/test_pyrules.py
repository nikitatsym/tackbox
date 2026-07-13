"""Step PYE: the pyrules flake8 plugin in its closed invocation form.

Fixtures are inline strings written to a temp dir (not on-disk under tests/) so
tackbox self-lint never encounters them. Each test runs
`flake8 --isolated --disable-noqa --select=TBX [--reporters=...]` exactly as the
tackbox engine will, and asserts the code, the carried rule id, the line, and
the exit contract.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _fixtures import PY_ONE_PER_RULE

_PY_DIR = str(Path(__file__).resolve().parents[1])

ALL_SEVEN = PY_ONE_PER_RULE

REPORTER = """def report_it(e):
    print(e)


def h():
    try:
        work()
    except ValueError as e:
        report_it(e)
"""


def _flake8(cwd, *names, reporters=None):
    argv = [
        sys.executable, "-m", "flake8",
        "--isolated", "--disable-noqa", "--select=TBX",
    ]
    if reporters is not None:
        argv.append(f"--reporters={reporters}")
    argv += list(names)
    env = dict(os.environ)
    env["PYTHONPATH"] = _PY_DIR
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text)
    return name


def test_every_python_rule_fires_with_its_id(tmp_path):
    _write(tmp_path, "v.py", ALL_SEVEN)
    r = _flake8(tmp_path, "v.py")
    assert r.returncode == 1, f"{r.stdout}\n{r.stderr}"
    for rule in (
        "python-swallowed-exception",
        "python-bare-except",
        "python-reraise-without-cause",
        "python-useless-except",
        "python-exit-in-except",
        "python-suppress-exception",
        "python-import-inside-function",
    ):
        assert rule in r.stdout, f"missing {rule}:\n{r.stdout}"


def test_output_code_is_tbx_and_carries_old_id(tmp_path):
    _write(tmp_path, "v.py", ALL_SEVEN)
    r = _flake8(tmp_path, "v.py")
    # flake8's selected CODE is TBXNNN; the message carries the id id-for-id.
    assert "TBX001 python-swallowed-exception:" in r.stdout, r.stdout


def test_swallowed_reports_on_the_try_line(tmp_path):
    src = "def h():\n    try:\n        work()\n    except ValueError as e:\n        pass\n"
    _write(tmp_path, "s.py", src)
    r = _flake8(tmp_path, "s.py")
    assert "s.py:2:" in r.stdout and "TBX001" in r.stdout, r.stdout


def test_marker_with_reason_suppresses(tmp_path):
    src = (
        "def cleanup():\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        # no-report: boundary cleanup, nothing to propagate\n        pass\n"
    )
    _write(tmp_path, "m.py", src)
    r = _flake8(tmp_path, "m.py")
    assert r.returncode == 0, r.stdout


def test_marker_empty_reason_does_not_suppress(tmp_path):
    src = (
        "def cleanup():\n    try:\n        work()\n"
        "    except ValueError as e:\n        # no-report:\n        pass\n"
    )
    _write(tmp_path, "m.py", src)
    r = _flake8(tmp_path, "m.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


def test_marker_block_above_first_body_stmt(tmp_path):
    # Reason spilled across a two-line block; bottom line sits above the stmt.
    src = (
        "def cleanup():\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        # no-report: boundary cleanup - the caller cannot act on\n"
        "        # this and there is nothing left to release\n        pass\n"
    )
    _write(tmp_path, "m.py", src)
    r = _flake8(tmp_path, "m.py")
    assert r.returncode == 0, r.stdout


def test_marker_above_survives_trailing_comment_on_anchor(tmp_path):
    # The anchor line's own trailing comment must not merge into the marker
    # block above it and shift the block's bottom line off the anchor.
    src = (
        "def cleanup():\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        # no-report: boundary cleanup, nothing to propagate\n"
        "        pass  # nothing else to release here\n"
    )
    _write(tmp_path, "m.py", src)
    r = _flake8(tmp_path, "m.py")
    assert r.returncode == 0, r.stdout


def test_trailing_marker_above_anchor_still_flags(tmp_path):
    # A marker trailing a code line is not a standalone block-above marker.
    src = (
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:  # no-report: this trails the except header\n"
        "        pass\n"
    )
    _write(tmp_path, "m.py", src)
    r = _flake8(tmp_path, "m.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


def test_shutdown_carveout_is_clean(tmp_path):
    src = (
        "import subprocess\n\n\ndef stop(proc):\n    try:\n"
        "        proc.wait(timeout=5)\n"
        "    except subprocess.TimeoutExpired:\n        proc.kill()\n"
    )
    _write(tmp_path, "c.py", src)
    r = _flake8(tmp_path, "c.py")
    assert r.returncode == 0, r.stdout


def test_mixed_tuple_is_not_carved(tmp_path):
    src = (
        "import subprocess\n\n\ndef stop(proc):\n    try:\n"
        "        proc.wait(timeout=5)\n"
        "    except (subprocess.TimeoutExpired, ValueError):\n        proc.kill()\n"
    )
    _write(tmp_path, "c.py", src)
    r = _flake8(tmp_path, "c.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


def test_tier2_declared_reporter_with_argflow_suppresses(tmp_path):
    _write(tmp_path, "rep.py", REPORTER)
    r = _flake8(tmp_path, "rep.py", reporters="rep.py#report_it")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_tier2_without_argflow_fires(tmp_path):
    src = (
        "def report_it():\n    pass\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_it()\n"
    )
    _write(tmp_path, "rep.py", src)
    r = _flake8(tmp_path, "rep.py", reporters="rep.py#report_it")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


def test_tier2_dead_symbol_exits_2(tmp_path):
    _write(tmp_path, "rep.py", REPORTER)
    r = _flake8(tmp_path, "rep.py", reporters="rep.py#nope")
    assert r.returncode == 2, f"{r.stdout}\n{r.stderr}"
    assert "no top-level function nope" in r.stderr, r.stderr


def test_bare_except_is_bare_not_swallowed(tmp_path):
    src = "def h():\n    try:\n        work()\n    except:\n        pass\n"
    _write(tmp_path, "b.py", src)
    r = _flake8(tmp_path, "b.py")
    assert "TBX003" in r.stdout and "TBX001" not in r.stdout, r.stdout


# --- built-in tier-1 reporters: the tackbox_report public capture API (D004) ---


def test_builtin_tier1_report_error_credits_without_declaration(tmp_path):
    # No --reporters flag and no marker: report_error is a built-in tier-1 sink,
    # and the caught flows into it, so the handler is credited.
    src = (
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        report_error('db down', cause=e, dedup_key='area.k')\n"
    )
    _write(tmp_path, "r.py", src)
    r = _flake8(tmp_path, "r.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_builtin_tier1_report_warn_and_panic_credit(tmp_path):
    # report_warn (cause keyword) and report_panic (caught as positional arg) are
    # both built-in tier-1 sinks.
    src = (
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_warn('transient', cause=e)\n\n\n"
        "def g():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_panic('loop', e)\n"
    )
    _write(tmp_path, "r.py", src)
    r = _flake8(tmp_path, "r.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_builtin_tier1_without_argflow_still_swallows(tmp_path):
    # The caught must flow into the call; a report_error that does not carry it is
    # not a capture of THIS error, so the handler still swallows.
    src = (
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_error('unrelated')\n"
    )
    _write(tmp_path, "r.py", src)
    r = _flake8(tmp_path, "r.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


def test_builtin_tier1_name_model_credits_same_named_local(tmp_path):
    # Documented D004 limitation: pyrules has no import origin, so a same-named
    # report_error from ANY module is credited - even this unrelated local def.
    # This is the Python name model's inherent false-positive-credit.
    src = (
        "def report_error(x):\n    print(x)\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_error(e)\n"
    )
    _write(tmp_path, "r.py", src)
    r = _flake8(tmp_path, "r.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


# --- TBX008 python-test-skip ---


def test_mark_skip_bare_flags(tmp_path):
    src = "import pytest\n\n\n@pytest.mark.skip\ndef test_x():\n    pass\n"
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 1 and "TBX008 python-test-skip" in r.stdout, r.stdout


def test_mark_skip_with_reason_clean(tmp_path):
    src = 'import pytest\n\n\n@pytest.mark.skip(reason="flaky upstream")\ndef test_x():\n    pass\n'
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 0, r.stdout


def test_mark_skip_empty_string_flags(tmp_path):
    src = 'import pytest\n\n\n@pytest.mark.skip("")\ndef test_x():\n    pass\n'
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 1 and "TBX008" in r.stdout, r.stdout


def test_mark_skip_positional_reason_clean(tmp_path):
    src = 'import pytest\n\n\n@pytest.mark.skip("flaky upstream")\ndef test_x():\n    pass\n'
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 0, r.stdout


def test_mark_skip_from_import_mark_flags(tmp_path):
    # `from pytest import mark` -> attribute chain ends in `mark.skip`.
    src = "from pytest import mark\n\n\n@mark.skip\ndef test_x():\n    pass\n"
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 1 and "TBX008" in r.stdout, r.stdout


def test_skipif_without_reason_flags(tmp_path):
    src = "import pytest\n\n\n@pytest.mark.skipif(True)\ndef test_x():\n    pass\n"
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 1 and "TBX008" in r.stdout, r.stdout


def test_skipif_with_reason_clean(tmp_path):
    src = 'import pytest\n\n\n@pytest.mark.skipif(True, reason="windows only")\ndef test_x():\n    pass\n'
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 0, r.stdout


def test_xfail_bare_flags(tmp_path):
    src = "import pytest\n\n\n@pytest.mark.xfail\ndef test_x():\n    pass\n"
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 1 and "TBX008" in r.stdout, r.stdout


def test_xfail_with_reason_clean(tmp_path):
    src = 'import pytest\n\n\n@pytest.mark.xfail(reason="known bug 123")\ndef test_x():\n    pass\n'
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 0, r.stdout


def test_pytest_skip_call_empty_flags(tmp_path):
    src = "import pytest\n\n\ndef test_x():\n    pytest.skip()\n"
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 1 and "TBX008" in r.stdout, r.stdout


def test_pytest_skip_call_blank_string_flags(tmp_path):
    src = 'import pytest\n\n\ndef test_x():\n    pytest.skip("")\n'
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 1 and "TBX008" in r.stdout, r.stdout


def test_pytest_skip_call_reason_clean(tmp_path):
    src = 'import pytest\n\n\ndef test_x():\n    pytest.skip("needs docker")\n'
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 0, r.stdout


def test_pytest_skip_call_variable_reason_trusted(tmp_path):
    src = "import pytest\n\n\ndef test_x():\n    pytest.skip(msg_var)\n"
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 0, r.stdout


def test_unittest_bare_skip_flags(tmp_path):
    src = "from unittest import skip\n\n\n@skip\ndef test_x():\n    pass\n"
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 1 and "TBX008" in r.stdout, r.stdout


def test_unittest_skip_with_reason_clean(tmp_path):
    src = 'import unittest\n\n\n@unittest.skip("slow on ci")\ndef test_x():\n    pass\n'
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 0, r.stdout


def test_bare_skip_without_unittest_import_clean(tmp_path):
    # Origin gate: a locally-defined `@skip` is not unittest's skip.
    src = "def skip(fn):\n    return fn\n\n\n@skip\ndef test_x():\n    pass\n"
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 0, r.stdout


def test_aliased_unittest_skip_does_not_gate_local_skip(tmp_path):
    # `skip as s` binds `s`; the bare name `skip` here is the local decorator.
    src = (
        "from unittest import skip as s\n\n\ndef skip(fn):\n    return fn\n\n\n"
        "@skip\ndef test_x():\n    pass\n"
    )
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 0, r.stdout


def test_skip_marker_above_decorator_suppresses(tmp_path):
    src = (
        "import pytest\n\n\n"
        "# test-skip: known flaky, tracked upstream\n"
        "@pytest.mark.skip\ndef test_x():\n    pass\n"
    )
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 0, r.stdout


def test_skip_marker_anchors_to_flagged_decorator(tmp_path):
    # Anchoring choice: the marker block sits above the flagged decorator's
    # own line, not above the first decorator of the def.
    src = (
        "import pytest\n\n\n"
        '@pytest.mark.parametrize("n", [1])\n'
        "# test-skip: known flaky, tracked upstream\n"
        "@pytest.mark.skip\ndef test_x(n):\n    pass\n"
    )
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 0, r.stdout


def test_skip_marker_empty_reason_flags(tmp_path):
    src = "import pytest\n\n\n# test-skip:\n@pytest.mark.skip\ndef test_x():\n    pass\n"
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 1 and "TBX008" in r.stdout, r.stdout


def test_skip_marker_blank_line_breaks_adjacency(tmp_path):
    src = (
        "import pytest\n\n\n"
        "# test-skip: reason\n\n"
        "@pytest.mark.skip\ndef test_x():\n    pass\n"
    )
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 1 and "TBX008" in r.stdout, r.stdout


def test_skip_marker_above_call_suppresses(tmp_path):
    src = (
        "import pytest\n\n\ndef test_x():\n"
        "    # test-skip: env not present locally\n    pytest.skip()\n"
    )
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 0, r.stdout


def test_no_report_marker_does_not_suppress_skip(tmp_path):
    # The no-report channel must not leak into the test-skip channel.
    src = (
        "import pytest\n\n\n"
        "# no-report: wrong channel\n"
        "@pytest.mark.skip\ndef test_x():\n    pass\n"
    )
    _write(tmp_path, "t.py", src)
    r = _flake8(tmp_path, "t.py")
    assert r.returncode == 1 and "TBX008" in r.stdout, r.stdout

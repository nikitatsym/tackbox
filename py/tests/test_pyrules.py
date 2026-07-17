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


# --- tier-1 reporters recognized by import origin (D004/D010) ---


def test_tier1_report_error_credits_without_declaration(tmp_path):
    # No --reporters flag and no marker: report_error is a tier-1 sink recognized
    # by its tackbox_report import origin, and the caught flows into it, so the
    # handler is credited.
    src = (
        "from tackbox_report import report_error\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        report_error('db down', cause=e, dedup_key='area.k')\n"
    )
    _write(tmp_path, "r.py", src)
    r = _flake8(tmp_path, "r.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_tier1_report_warn_and_panic_credit(tmp_path):
    # report_warn (cause keyword) and report_panic (caught as positional arg) are
    # both tier-1 sinks by origin. report_warn carries a dedup_key (D008); panic
    # takes no dedup_key.
    src = (
        "from tackbox_report import report_warn, report_panic\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_warn('transient', cause=e, dedup_key='task.transient')\n\n\n"
        "def g():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_panic('loop', e)\n"
    )
    _write(tmp_path, "r.py", src)
    r = _flake8(tmp_path, "r.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_tier1_without_argflow_still_swallows(tmp_path):
    # The caught must flow into the call; a report_error that does not carry it is
    # not a capture of THIS error, so the handler still swallows.
    src = (
        "from tackbox_report import report_error\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_error('unrelated')\n"
    )
    _write(tmp_path, "r.py", src)
    r = _flake8(tmp_path, "r.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


def test_shadow_attack_local_def_is_not_credited(tmp_path):
    # Origin model (reworked from the old name-model false-credit fixture, D010):
    # a module-level def report_error rebinds the imported name from that point,
    # so the call in h is NOT the verb and the silent catch fires TBX001. The
    # shadow attack self-defeats.
    src = (
        "from tackbox_report import report_error\n\n\n"
        "def report_error(x):\n    print(x)\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n        report_error(e)\n"
    )
    _write(tmp_path, "r.py", src)
    r = _flake8(tmp_path, "r.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


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


# --- TBX010 notify gate + double-lane (D006) ---


def test_notify_narrow_except_is_clean(tmp_path):
    # A notify carrying the caught error on a narrow except routes it to the user
    # lane (gate satisfied): credited for TBX001, no TBX010.
    src = (
        "from tackbox_report import notify\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ConnectionError as e:\n"
        "        notify('connection lost, retrying', cause=e, dedup_key='net.offline')\n"
    )
    _write(tmp_path, "n.py", src)
    r = _flake8(tmp_path, "n.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_notify_broad_except_fires(tmp_path):
    # A notify in a broad `except Exception` routes every failure to the user
    # lane and blinds telemetry - the gate finding.
    src = (
        "from tackbox_report import notify\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except Exception as e:\n"
        "        notify('connection lost, retrying', cause=e, dedup_key='net.offline')\n"
    )
    _write(tmp_path, "n.py", src)
    r = _flake8(tmp_path, "n.py")
    assert r.returncode == 1 and "TBX010" in r.stdout, r.stdout


def test_notify_without_argflow_still_swallows(tmp_path):
    # A notify the caught error does not reach is not credited: still a swallow,
    # and not the notify gate (it is not terminating this failure path).
    src = (
        "from tackbox_report import notify\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        notify('connection lost, retrying', cause=other, dedup_key='net.offline')\n"
    )
    _write(tmp_path, "n.py", src)
    r = _flake8(tmp_path, "n.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


def test_notify_double_lane_fires(tmp_path):
    # capture + notify on one path in a narrow except: error already reaches the
    # user lane, so the notify double-shows.
    src = (
        "from tackbox_report import report_error, notify\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ConnectionError as e:\n"
        "        report_error('server unreachable', cause=e, dedup_key='net.fail')\n"
        "        notify('connection lost, retrying', cause=e, dedup_key='net.offline')\n"
    )
    _write(tmp_path, "n.py", src)
    r = _flake8(tmp_path, "n.py")
    assert r.returncode == 1 and "TBX010" in r.stdout, r.stdout


def test_match_exclusive_cases_clean(tmp_path):
    # notify in one match case, capture in the exclusive capture-all case: only
    # one runs, so no single path both captures and notifies.
    src = (
        "from tackbox_report import report_error, notify\n\n\n"
        "def h(status):\n    try:\n        work()\n"
        "    except ConnectionError as e:\n"
        "        match status:\n"
        "            case 503:\n"
        "                notify('connection lost, retrying', cause=e, dedup_key='net.offline')\n"
        "            case _:\n"
        "                report_error('server unreachable', cause=e, dedup_key='net.fail')\n"
    )
    _write(tmp_path, "n.py", src)
    r = _flake8(tmp_path, "n.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_match_same_case_double_lane_fires(tmp_path):
    # capture and notify in the SAME match case run on one path: double-lane.
    src = (
        "from tackbox_report import report_error, notify\n\n\n"
        "def h(status):\n    try:\n        work()\n"
        "    except ConnectionError as e:\n"
        "        match status:\n"
        "            case 503:\n"
        "                report_error('server unreachable', cause=e, dedup_key='net.fail')\n"
        "                notify('connection lost, retrying', cause=e, dedup_key='net.offline')\n"
        "            case _:\n"
        "                raise\n"
    )
    _write(tmp_path, "n.py", src)
    r = _flake8(tmp_path, "n.py")
    assert r.returncode == 1 and "TBX010" in r.stdout, r.stdout


def test_notify_loop_then_capture_double_lane_fires(tmp_path):
    # notify inside a loop, capture after it: a loop body may run alongside the
    # post-loop capture, so the pair stays a double-lane.
    src = (
        "from tackbox_report import report_error, notify\n\n\n"
        "def h(items):\n    try:\n        work()\n"
        "    except ConnectionError as e:\n"
        "        for _ in items:\n"
        "            notify('connection lost, retrying', cause=e, dedup_key='net.offline')\n"
        "        report_error('server unreachable', cause=e, dedup_key='net.fail')\n"
    )
    _write(tmp_path, "n.py", src)
    r = _flake8(tmp_path, "n.py")
    assert r.returncode == 1 and "TBX010" in r.stdout, r.stdout


# --- D-1 path-sensitive swallow: a handled leg must not credit its complement ---


def test_conditional_notify_silent_complement_fires(tmp_path):
    # notify narrowed under `if cond` whose complement does nothing with the
    # caught error: the notify credits only its path, the fall-through swallows.
    src = (
        "from tackbox_report import notify\n\n\n"
        "def h(cond):\n    try:\n        work()\n"
        "    except ConnectionError as e:\n"
        "        if cond:\n"
        "            notify('connection lost, retrying', cause=e, dedup_key='net.offline')\n"
        "            return\n"
        "        # cond False: e neither captured nor notified\n"
    )
    _write(tmp_path, "p.py", src)
    r = _flake8(tmp_path, "p.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


def test_conditional_capture_silent_complement_fires(tmp_path):
    # same shape with a capture: the capture credits only its path.
    src = (
        "from tackbox_report import report_error\n\n\n"
        "def h(cond):\n    try:\n        work()\n"
        "    except ConnectionError as e:\n"
        "        if cond:\n"
        "            report_error('server unreachable', cause=e, dedup_key='net.fail')\n"
        "            return\n"
        "        # cond False: e swallowed\n"
    )
    _write(tmp_path, "p.py", src)
    r = _flake8(tmp_path, "p.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


def test_conditional_notify_else_capture_clean(tmp_path):
    # notify in one leg, capture in the exclusive else leg: every path handled,
    # and the legs are exclusive so no double-lane.
    src = (
        "from tackbox_report import report_error, notify\n\n\n"
        "def h(cond):\n    try:\n        work()\n"
        "    except ConnectionError as e:\n"
        "        if cond:\n"
        "            notify('connection lost, retrying', cause=e, dedup_key='net.offline')\n"
        "        else:\n"
        "            report_error('server unreachable', cause=e, dedup_key='net.fail')\n"
    )
    _write(tmp_path, "p.py", src)
    r = _flake8(tmp_path, "p.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_conditional_notify_fallthrough_capture_clean(tmp_path):
    # notify+return on the guarded path, capture on the fall-through: both paths
    # handled, and the return keeps the two lanes exclusive.
    src = (
        "from tackbox_report import report_error, notify\n\n\n"
        "def h(cond):\n    try:\n        work()\n"
        "    except ConnectionError as e:\n"
        "        if cond:\n"
        "            notify('connection lost, retrying', cause=e, dedup_key='net.offline')\n"
        "            return\n"
        "        report_error('server unreachable', cause=e, dedup_key='net.fail')\n"
    )
    _write(tmp_path, "p.py", src)
    r = _flake8(tmp_path, "p.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


# --- TBX011 msg-static (D007) + dedup_key (D008) ---


def test_reporter_dynamic_msg_fires(tmp_path):
    src = (
        "from tackbox_report import report_error\n\n\n"
        "def h(m):\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        report_error(m, cause=e, dedup_key='area.key')\n"
    )
    _write(tmp_path, "m.py", src)
    r = _flake8(tmp_path, "m.py")
    assert r.returncode == 1 and "TBX011" in r.stdout, r.stdout


def test_reporter_dedup_missing_fires(tmp_path):
    src = (
        "from tackbox_report import report_error\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        report_error('db write failed', cause=e)\n"
    )
    _write(tmp_path, "d.py", src)
    r = _flake8(tmp_path, "d.py")
    assert r.returncode == 1 and "TBX011" in r.stdout, r.stdout


def test_reporter_dedup_not_literal_fires(tmp_path):
    src = (
        "from tackbox_report import report_error\n\n\n"
        "def h(k):\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        report_error('db write failed', cause=e, dedup_key=k)\n"
    )
    _write(tmp_path, "d.py", src)
    r = _flake8(tmp_path, "d.py")
    assert r.returncode == 1 and "TBX011" in r.stdout, r.stdout


def test_reporter_dedup_bad_format_fires(tmp_path):
    src = (
        "from tackbox_report import report_error\n\n\n"
        "def h():\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        report_error('db write failed', cause=e, dedup_key='BadKey')\n"
    )
    _write(tmp_path, "d.py", src)
    r = _flake8(tmp_path, "d.py")
    assert r.returncode == 1 and "TBX011" in r.stdout, r.stdout


def test_quiet_dynamic_msg_clean_dedup_validated(tmp_path):
    # quiet is telemetry-only: msg-static (D007) does not apply, but the
    # dedup_key (D008) still does - a valid literal key keeps it clean.
    src = (
        "from tackbox_report import report_quiet\n\n\n"
        "def h(m):\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        report_quiet(m, cause=e, dedup_key='cache.refresh')\n"
    )
    _write(tmp_path, "q.py", src)
    r = _flake8(tmp_path, "q.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_dynamic_dedup_in_test_file_clean(tmp_path):
    # D-4: TBX011 (and TBX010) skip test files - a dynamic dedup_key in a
    # test_*.py is clean; the swallow rule still credits the capture.
    src = (
        "from tackbox_report import report_error\n\n\n"
        "def test_h(k):\n    try:\n        work()\n"
        "    except ValueError as e:\n"
        "        report_error('db write failed', cause=e, dedup_key=k)\n"
    )
    _write(tmp_path, "test_x.py", src)
    r = _flake8(tmp_path, "test_x.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_broad_notify_in_test_file_clean(tmp_path):
    # D-4: the notify gate (TBX010) skips test files - a broad notify is clean.
    src = (
        "from tackbox_report import notify\n\n\n"
        "def test_h():\n    try:\n        work()\n"
        "    except Exception as e:\n"
        "        notify('connection lost, retrying', cause=e, dedup_key='net.offline')\n"
    )
    _write(tmp_path, "test_x.py", src)
    r = _flake8(tmp_path, "test_x.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_owner_package_self_credits_and_skips_arg_checks(tmp_path):
    # Owner package (a tackbox_report/ path segment, D010): its own top-level verb
    # defs ARE the origin, so an internal broad-except calling report_error with a
    # computed panic:<name> dedup_key self-credits (no TBX001) and TBX010/TBX011 do
    # not bind the owner - zero findings, no marker.
    src = (
        "def report_error(msg, cause=None, tags=None, dedup_key=''):\n    pass\n\n\n"
        "def report_panic(name):\n    try:\n        work()\n"
        "    except Exception as e:\n"
        "        report_error('capture failed', cause=e, dedup_key=f'panic:{name}')\n"
    )
    pkg = tmp_path / "tackbox_report"
    pkg.mkdir()
    (pkg / "lib.py").write_text(src)
    r = _flake8(tmp_path, "tackbox_report/lib.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


# --- D009 marker reason floor ---


def test_marker_short_reason_does_not_suppress(tmp_path):
    # 9-char reason is too cheap: the swallow still fires.
    src = (
        "def cleanup():\n    try:\n        work()\n"
        "    except ValueError as e:\n        # no-report: too short\n        pass\n"
    )
    _write(tmp_path, "m.py", src)
    r = _flake8(tmp_path, "m.py")
    assert r.returncode == 1 and "TBX001" in r.stdout, r.stdout


def test_marker_ten_char_reason_suppresses(tmp_path):
    src = (
        "def cleanup():\n    try:\n        work()\n"
        "    except ValueError as e:\n        # no-report: cleanup ok\n        pass\n"
    )
    _write(tmp_path, "m.py", src)
    r = _flake8(tmp_path, "m.py")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"

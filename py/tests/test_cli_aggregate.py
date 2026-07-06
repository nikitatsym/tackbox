"""Unit tests for CLI exit-code aggregation.

erclint's `-json` mode returns exit 0 even with findings; the CLI
promotes that to nonzero. Other engines' exit codes flow through.
"""

from __future__ import annotations

from tackbox.cli import _aggregate_exit
from tackbox.engines import EngineResult


def _r(engine_id: str, exit_code: int, stdout: str = "", stderr: str = "") -> EngineResult:
    return EngineResult(
        engine_id=engine_id,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def test_all_zero_stays_zero():
    assert _aggregate_exit([_r("tackbox-eslint", 0), _r("tackbox-mdlint", 0)]) == 0


def test_max_of_nonzero_wins():
    assert _aggregate_exit([_r("tackbox-eslint", 1), _r("tackbox-mdlint", 2)]) == 2


def test_signal_kill_code_dominates():
    assert _aggregate_exit([_r("tackbox-eslint", 137), _r("tackbox-mdlint", 1)]) == 137


def test_erclint_exit_zero_with_empty_json_stays_zero():
    result = _r("erclint", 0, stdout="{}\n")
    assert _aggregate_exit([result]) == 0


def test_erclint_exit_zero_with_findings_promotes_to_one():
    finding = (
        '{"fixture/pkga": {"errcheck": [{"posn": "a", "end": "a", "message": "m"}]}}'
    )
    result = _r("erclint", 0, stdout=finding)
    assert _aggregate_exit([result]) == 1


def test_erclint_exit_zero_with_analyzer_error_promotes_to_one():
    payload = '{"pkga": {"errcheck": {"error": "load failed"}}}'
    result = _r("erclint", 0, stdout=payload)
    assert _aggregate_exit([result]) == 1


def test_erclint_nonzero_exit_passthrough_without_promotion_math():
    # If erclint itself crashed (nonzero exit) the aggregate takes its code.
    result = _r("erclint", 2, stdout="")
    assert _aggregate_exit([result]) == 2


def test_promotion_only_for_erclint_not_other_engines():
    finding_shape = (
        '{"pkg": {"errcheck": [{"posn": "a", "end": "a", "message": "m"}]}}'
    )
    # A hypothetical zero-exit engine with findings-shaped stdout should NOT be promoted.
    result = _r("tackbox-eslint", 0, stdout=finding_shape)
    assert _aggregate_exit([result]) == 0


def test_javalint_exit_zero_with_findings_promotes_to_one():
    # javalint mirrors erclint: exit 0 even with findings (erclint-shaped JSON).
    finding = (
        '{"java/Foo.java": {"JV001": [{"posn": "java/Foo.java:3:5", '
        '"end": "java/Foo.java:3:5", "message": "m"}]}}'
    )
    assert _aggregate_exit([_r("javalint", 0, stdout=finding)]) == 1


def test_javalint_exit_zero_with_empty_json_stays_zero():
    assert _aggregate_exit([_r("javalint", 0, stdout="{}\n")]) == 0

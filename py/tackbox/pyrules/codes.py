"""Selected flake8 codes, stable rule ids, and default TBX messages."""

from __future__ import annotations

# TBXNNN -> the pre-migration opengrep rule id (parity: findings stay id-for-id).
# TBX009 is retired (secret-name heuristic removed, D001) and must not be reused.
CODE_TO_ID: dict[str, str] = {
    "TBX001": "python-swallowed-exception",
    "TBX002": "python-suppress-exception",
    "TBX003": "python-bare-except",
    "TBX004": "python-reraise-without-cause",
    "TBX005": "python-useless-except",
    "TBX006": "python-import-inside-function",
    "TBX007": "python-exit-in-except",
    "TBX008": "python-test-skip",
    "TBX010": "python-notify-lane",
    "TBX011": "python-reporter-args",
    "TBX012": "python-unused-private",
    "F401": "python-unused-import",
    "F841": "python-unused-variable",
}

PYFLAKES_CODES = tuple(code for code in CODE_TO_ID if not code.startswith("TBX"))


MESSAGES: dict[str, str] = {
    "TBX001": "let the exception propagate or wrap+reraise via raise ... from e",
    "TBX002": "restructure so the error cannot raise instead of suppressing it",
    "TBX003": "bare except catches KeyboardInterrupt/SystemExit; catch a specific exception type",
    "TBX004": "raise in except without from e discards the traceback; use raise NewError(...) from e",
    "TBX005": "except that only re-raises is a no-op; remove the try/except",
    "TBX006": "Import inside a function; move it to module top",
    "TBX007": "sys.exit inside except masks the original error; let the exception propagate",
    "TBX008": "Skipped/xfailed test without a reason is a silent hole in the suite; pass a non-empty reason",
    # TBX010 default arm = the notify narrowing gate; the double-lane arm passes
    # its own message via the finding detail.
    "TBX010": (
        "notify in a broad except routes every failure to the user lane and blinds telemetry; "
        "catch a narrow exception type, or use report_error/report_warn; a new # no-report: marker "
        "needs user approval"
    ),
    # TBX011 default arm = msg-static (D007); the dedup_key arms (D008) pass their
    # own message via the finding detail.
    "TBX011": "msg must be a static string literal; dynamic data belongs in cause and tags",
}

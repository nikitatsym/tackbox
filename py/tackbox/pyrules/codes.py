"""TBX rule codes, their canonical rule ids, and one-line messages.

Single source of truth for the code<->id mapping: the flake8 plugin emits
`TBXNNN <id>: <message>` and the CLI's machine parser maps the TBX code back
to the id via CODE_TO_ID. Messages are one line, ~100 chars: the violated
invariant plus the primary fix; details and rationale live in the rule docs.
"""

from __future__ import annotations

# TBXNNN -> the pre-migration opengrep rule id (parity: findings stay id-for-id).
CODE_TO_ID: dict[str, str] = {
    "TBX001": "python-swallowed-exception",
    "TBX002": "python-suppress-exception",
    "TBX003": "python-bare-except",
    "TBX004": "python-reraise-without-cause",
    "TBX005": "python-useless-except",
    "TBX006": "python-import-inside-function",
    "TBX007": "python-exit-in-except",
    "TBX008": "python-test-skip",
    "TBX009": "erc006-fingerprint-secret-arg",
}


MESSAGES: dict[str, str] = {
    "TBX001": "let the exception propagate or wrap+reraise via raise ... from e",
    "TBX002": "restructure so the error cannot raise instead of suppressing it",
    "TBX003": "bare except catches KeyboardInterrupt/SystemExit; catch a specific exception type",
    "TBX004": "raise in except without from e discards the traceback; use raise NewError(...) from e",
    "TBX005": "except that only re-raises is a no-op; remove the try/except",
    "TBX006": "Import inside a function; move it to module top",
    "TBX007": "sys.exit inside except masks the original error; let the exception propagate",
    "TBX008": "Skipped/xfailed test without a reason is a silent hole in the suite; pass a non-empty reason",
    "TBX009": "capture argument names a secret (token/password/key/secret/cookie); do not pass it to a reporter",
}

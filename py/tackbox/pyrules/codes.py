"""TBX rule codes, their canonical rule ids, and verbatim messages.

Single source of truth for the code<->id mapping: the flake8 plugin emits
`TBXNNN <id>: <message>` and the CLI's machine parser maps the TBX code back
to the id via CODE_TO_ID. Messages are the exceptions-python.yaml text carried
verbatim (only newlines flattened to spaces so flake8's one-line-per-finding
format holds); they are model-facing teach-text - the remedy list is the point.
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
}


def _flat(text: str) -> str:
    return " ".join(text.split())


MESSAGES: dict[str, str] = {
    "TBX001": _flat(
        "`except` block has no `raise` - exception is silently swallowed. "
        "Fail-fast: let it propagate (remove the except) or wrap+reraise "
        "with context via `raise NewError(...) from e`."
    ),
    "TBX002": _flat(
        "`contextlib.suppress(...)` silently drops the exception - no log, "
        "no record, no rethrow. By default this is a cosmetic dodge of the "
        "python-swallowed-exception rule. Restructure the code so the "
        "exception cannot raise (precondition guards, atomic state tracking, "
        "narrower syscall sequence), or - if there is a legitimate "
        "async/cleanup boundary - use try/except with a `# no-report: "
        "<reason>` marker. Narrowly allowlisted (documented control-flow "
        "signal, not a dodge): asyncio.CancelledError around `await task` "
        "after task.cancel() - the CancelledError on the await IS the "
        "confirmation that cancel propagated, not an error to log."
    ),
    "TBX003": _flat(
        "Bare `except:` / `except BaseException:` catches KeyboardInterrupt, "
        "SystemExit, MemoryError - things you never want to swallow. Catch "
        "a specific exception type."
    ),
    "TBX004": _flat(
        "Raising a new exception in `except` without `from $E` (or "
        "`from None`) discards the original traceback. Use "
        "`raise NewError(...) from $E` to preserve the chain."
    ),
    "TBX005": _flat(
        "`except` that only re-raises the caught exception is a no-op "
        "wrapper. Remove the try/except and let the exception propagate "
        "naturally."
    ),
    "TBX006": _flat(
        "Import inside a function/method. Imports must live at the top of "
        "the file (after module docstring / `from __future__`). Move it "
        "out - lazy imports hide dependencies and slow first call."
    ),
    "TBX007": _flat(
        "`sys.exit` / `os._exit` inside `except` masks the original error. "
        "Exit code tells nothing about what actually failed. Let the "
        "exception propagate."
    ),
}

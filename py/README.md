# pyrules (Python)

Python exception and test-skip rules ship as the `pyrules` flake8 plugin
(`TBX` codes) inside the `tackbox` wheel, run by the CLI - there is no
separate install:

```bash
uvx tackbox@latest lint .
```

## Rules

The `pyrules` flake8 plugin and selected pyflakes checks map their codes
to stable rule ids in human, machine, CodeClimate, and hook output. Flake8
runs isolated with `noqa` disabled and the fixed selection `TBX,F401,F841`;
consumer flake8 configuration does not change the bundle.

| Code | Rule | Summary |
| --- | --- | --- |
| TBX001 | swallowed-exception | propagate or wrap via `raise ... from e` |
| TBX002 | suppress-exception | restructure so it can't raise |
| TBX003 | bare-except | catch a specific type, not bare |
| TBX004 | reraise-without-cause | keep the cause via `raise ... from e` |
| TBX005 | useless-except | drop a try/except that only re-raises |
| TBX006 | import-inside-function | move the import to module top |
| TBX007 | exit-in-except | don't `sys.exit` in except; propagate |
| TBX008 | test-skip | a skipped/xfailed test needs a reason |
| TBX010 | notify-lane | notify needs a narrow except type |
| TBX011 | reporter-args | literal msg and dedup key; data in cause/tags |
| TBX012 | unused-private | delete unused module-private code or make a cross-module API public |
| F401 | unused-import | remove unused imports; declare re-exports in `__all__` |
| F841 | unused-variable | remove unused local assignments; only bare `_` is a discard |

Full ids carry the `python-` prefix (e.g. `python-swallowed-exception`).

A skip reason is accepted in any of the natural forms:
`@pytest.mark.skip(reason=...)`,
`@pytest.mark.skipif(cond, reason=...)`,
`@pytest.mark.xfail(reason=...)`, `pytest.skip(...)`, or
`@unittest.skip(...)`. `contextlib.suppress` is flagged as a
cosmetic dodge of the swallow rule; the one allowlisted use is
`asyncio.CancelledError` around `await task` after `task.cancel()`,
where the CancelledError on the await IS the confirmation that the
cancel propagated, not an error to log. The notify gate (TBX010) and
the user-lane argument contract - static-literal msg, well-formed
`dedup_key` (TBX011) - apply to the `tackbox_report` verbs recognized
by import origin (D010).

Unused-binding conventions and limits are in
[D021](../rules/DECISIONS.md#d021---unused-bindings-are-a-mandatory-lane-2026-10-10).

## Capture recognition

Python resolves tier-1 by import origin (D010), scoped to the fixed
`tackbox_report` package (`report_error` /
`report_warn` / `report_quiet` / `report_panic` / `notify`): a call
counts only when it resolves through the module's own import bindings -
`from tackbox_report import ...` or `import tackbox_report` (attribute
form included) - so a same-named local def or a foreign import is not
the verb. Tier-2 declarations in `.tackbox/reporters`
([reporter recognition](../docs/rules.md#reporter-recognition)) stay matched
by function name (any same-named call), not by resolving the callee to its
file. The helper library is [tackbox-report](tackbox_report/README.md).

# javalint (Java)

Error reporting coverage, test integrity, and unused binding checks for Java.
javalint ships inside the `tackbox` wheel and runs as part of `tackbox lint`
([requirements](../README.md#requirements)).

## Rules

| Code | Name | Rule |
| --- | --- | --- |
| JV001 | swallow | every catch path must propagate, report, print, or carry `// no-report` |
| JV002 | chain | a thrown exception must carry the caught as its cause |
| JV003 | throwable | a catch of `Throwable` / `Error` must rethrow |
| JV004 | useless-catch | a catch that only rethrows the caught unchanged - deleted, not annotated |
| JV005 | exit | `System.exit` in a catch needs a preceding capture |
| JV006 | double-capture | no path may both report and rethrow, nor both capture and notify |
| JV007 | skip | `@Disabled` / `@Ignore` must carry a non-empty reason string |
| JV009 | notify gate | a notify in a broad catch must narrow the type |
| JV010 | reporter args | a Report user-lane verb needs a static-literal msg and a well-formed literal dedupKey |
| JV011 | unused locals | unused local variables (PMD) |
| JV012 | unused private fields | unused private fields (PMD) |
| JV013 | unused private methods | unused private methods (PMD) |
| JV014 | unnecessary imports | unused, duplicate, and redundant (`java.lang`, current package) imports (PMD) |

For JV014, Javadoc references follow PMD's import analysis. Unused-binding conventions and limits are in
[D021](../rules/DECISIONS.md#d021---unused-bindings-are-a-mandatory-lane-2026-10-10).

## Capture recognition

A Java capture counts when the caught exception reaches a
`nl.tsym.tackbox.report.Report` call or a known logger sink (e.g. slf4j,
`java.lang.System.Logger`) at `ERROR` / `WARNING`. Declared sinks:
[reporter recognition](../docs/rules.md#reporter-recognition). The helper
library is [report](report/README.md).

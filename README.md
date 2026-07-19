# tackbox

![tackbox logo](assets/logo-round.png)

[![publish](https://github.com/nikitatsym/tackbox/actions/workflows/publish.yml/badge.svg)](https://github.com/nikitatsym/tackbox/actions/workflows/publish.yml)
[![verify-release](https://github.com/nikitatsym/tackbox/actions/workflows/verify-release.yml/badge.svg)](https://github.com/nikitatsym/tackbox/actions/workflows/verify-release.yml)
[![pypi](https://raw.githubusercontent.com/nikitatsym/tackbox/badges/pypi.svg)](https://pypi.org/project/tackbox/)

**Every failure must report, propagate, or explain itself.**

tackbox is a guardrail for developers and coding agents. It catches common
local ways failures are hidden by accident, haste, or expediency and
requires an explicit outcome: propagate, report, or explain. It is not a
whole-program proof or a security boundary.

tackbox's lint contract recognizes direct reporting helpers as an explicit
report outcome. Execution policy, control flow, result ownership, and runtime
integration remain application decisions.

Coding agents write error handling that looks right and silently
isn't: a swallowed exception, a fatal exit with nothing logged, a
report with the cause stripped out. tackbox catches it the moment
it's written: hooked into the agent's edit loop it flags the finding
before the turn ends, and the same rules gate pre-commit and CI -
one coverage bar for hand-written and agent-written code.

There are no per-rule disable flags. A local exception stays visible
as a reasoned `// no-report: <reason>` marker at the site, and every
marker must be covered by a line in the committed approval manifest
(`.tackbox/approvals`). Adding that line is the act that draws the
approval ask in an agent session; an uncovered marker keeps lint,
`dev.py check`, and CI red until it is approved or reverted.

```go
resp, err := client.Do(req)
if err != nil {
    return nil // looks handled; the failure just vanished
}
```

```text
client.go:42: ERC001: err-branch must propagate, capture, or carry
the error into a terminal exit (err=err)
```

One command brings the whole stack across Go, Python, Java, JS, TS,
Svelte, and Markdown - no `go install`, no `npm i`, no external
`opengrep`:

```bash
uvx tackbox@latest lint .
```

The wheel is hermetic: a consumer needs only `git` on PATH (plus a Go
toolchain if the repo has `.go` files, and a Java 17+ runtime if it
has `.java` files) and, the first time a given engine version runs,
network access to fetch the engine payload once.
Rules roll out via `@latest` - a new safety rule reaches every repo on
its next run.

## What it catches

- **Swallowed errors** - the `catch {}` or `if err != nil { return nil }`
  that makes a failure vanish. Every path must report, propagate, or
  carry an explicit `// no-report: <reason>`.
- **Silent exits** - `os.Exit`, `log.Fatal`, `System.exit`, or a local
  `die` reached with an unreported error, so the process dies and your
  error tracker never hears about it.
- **Double reports** - capturing an error *and* re-throwing it, so the
  same failure hits Sentry/glitchtip twice and drowns the signal.
- **Broken cause chains** - a new exception thrown from a `catch` that
  drops the original (only its message survives), erasing the stack
  you'd actually debug from.
- **Silently killed tests** - the `it.skip` with no explanation, the
  failing test reborn as `test.todo`, the `it.only` that quietly turns
  off the rest of the suite. Every skip must state a reason; focused
  tests are always an error.

## Wiring into a repo

Call `tackbox lint` from the repo's `dev.py lint`, next to the
project's own linters:

```python
def lint():
    sh("uvx tackbox@latest lint .")
    sh("uv run ruff check .")   # project-owned, if Python
```

Pre-commit runs a single language-agnostic hook; `dev.py check`
(= lint + test) decides what to scan:

```yaml
# .pre-commit-config.yaml in the consumer repo
repos:
  - repo: local
    hooks:
      - id: dev-check
        name: dev.py check
        entry: python3
        args: [dev.py, check]
        language: system
        pass_filenames: false
        always_run: true
```

## CodeClimate report

`tackbox lint --codequality <path>` also writes a CodeClimate-format JSON
array of every finding to `<path>` (console output and exit code unchanged;
the report is written even when findings exist). Wire it into GitLab CI as a
`codequality` report so the MR widget renders the findings:

```yaml
lint:
  script: uvx tackbox@latest lint . --codequality gl-code-quality.json
  artifacts:
    reports:
      codequality: gl-code-quality.json
```

## Lint scope and flags

`tackbox lint [path] [flags]` scans the git-tracked source set. The
positional `path` (default `.`) narrows the scan to a subtree; a path
matching no file in the source set is a usage error (exit 2).

- **`--changed`** limits the scan to the dirty tree: files staged,
  unstaged, or untracked.
- **`--since <ref>`** limits it to the three-dot diff `<ref>...HEAD`
  (what this branch changed since its merge-base with `<ref>`) unioned
  with the dirty tree, so it already covers `--changed`; passing both
  is the same scope as `--since` alone. An unknown ref, or a repo with
  no commits yet, is a usage error (exit 2), not a crash.
- **`--no-cache`** ignores the per-`(unit, engine)` result cache for
  this run and writes nothing back to it.

The `path` scope and the change filters compose:
`tackbox lint src --changed` lints only the dirty files under `src/`.

This scope filter is unrelated to the `escapes` command's `--since`
`<rev>`, which selects inventory entries new against a revision.

The approvals consistency check (see Approval manifest) is exempt
from all scope filters: it always covers the whole tree and reports
under an `approvals (whole tree):` header, scoped runs included - a
scoped CI lint cannot scope the wall away.

## Exit codes

Across commands, `2` is a usage or setup error the command cannot run
past (argparse misuse, and the per-command cases below).

- **lint** - `0` clean, `1` one or more findings, `2` a scope matching
  no files or a git/engine setup failure (a bad `--changed` / `--since`
  ref; an engine-store, reporters, or `go list` error). A closed
  downstream pipe (`lint | head`) exits `141`; `--codequality` never
  changes the code.
- **doctor** - `0` all checks pass, `1` at least one failed; every
  check always runs (no short-circuit).
- **approvals** - `0` consistent, `2` inconsistent (uncovered
  markers, orphaned entries, or unresolvable files), `1` infra.
  `--draft` is a generator, not a gate: `0` when every uncovered
  marker was drafted (an orphan-only tree included), `2` only when
  unresolvable files leave the draft incomplete.
- **hook** - `0` a no-op, a clean event, or a JSON decision (a
  PreToolUse approval prompt or a PostToolUse Bash block); `1` a
  non-blocking infra error (unreadable stdin, a git failure); `2` a
  PostToolUse finding on the edited lines, a non-compiling Go
  package, or an approvals inconsistency anywhere in the worktree,
  which blocks the edit in-loop.
- **escapes** - `0` whenever it runs, entries or not (an inventory,
  not a gate); `1` only for a bad `--since` rev.

## Distribution

`uvx tackbox@latest` installs one small wheel; the engine payload is
fetched separately and cached per version:

- `tackbox` (thin) - the Python CLI (including the `pyrules` flake8
  plugin), the `erclint` / `erclint-opengrep` binaries, the
  `javalint.jar`, the opengrep rule yamls, and the ESLint and
  markdownlint plugins and presets. Platform-specific, bumped on every
  push.
- `tackbox-engines` (fat, ~350 MB unpacked) - the bundled Node
  runtime, the `opengrep` binary, and the vendored third-party
  `node_modules`. Published as a PyPI wheel but **not** a pip
  dependency of thin. On the first run for a given engine version,
  tackbox resolves the wheel via the PyPI JSON API, verifies its
  unpacked payload against the tree sha256 pinned in the thin
  wheel's `engines.json`, and
  unpacks it once into `$XDG_DATA_HOME/tackbox/engines/<version>/`
  (default `~/.local/share/...`; override `TACKBOX_ENGINES_DIR`).
  Every later thin version reuses that one copy, so a stream of
  `@latest` patch bumps never re-materializes the engines. Bumped only
  when an engine changes.

After the first fetch tackbox runs fully offline until the engine
version changes. Platform wheels cover Linux x86_64/arm64 (manylinux),
macOS arm64, and Windows x86_64. `engines.json` in the thin
wheel records the source, version, sha256, and license of every
bundled binary and dependency; `tackbox doctor` fetches the store if
absent and verifies the payload against it.

## What the rules enforce

Covers ERC001-009 (Go, via `erclint`), JV001-010 (Java, via the native
`javalint` engine; JV008 is retired), Python exception, notify, and
test-skip rules (via the `pyrules` flake8 plugin), frontend swallow,
notify, and test-skip rules (JS, TS, Svelte, via ESLint), and Markdown
(MD001-060 + ASCII).

See `go/README.md` for the complete Go ruleset. Across supported
languages, the core policy is:

- Every `err != nil` branch must propagate, capture, or carry an
  explicit `// no-report: <reason>` marker.
- Common parser results that fall through to `nil` must capture or
  carry `// parse-skip: <reason>`.
- Terminal exits (`log.Fatal*`, `os.Exit`, project-local `die`) must
  be preceded by a capture call or carry a `// no-report: <reason>`
  marker (e.g. for the normal `os.Exit(0)` at the end of main).
- Bare `return nil` from a single-result function must carry
  `// nil-return: <reason>` or use `(val, ok)` / `(val, err)`.
- A single err-branch may not both capture and `return err`.
- The dedupKey must be a well-formed literal; in Go, capture-call
  arguments must additionally not carry raw user input (a
  `*http.Request` field).
- A `notify` (user lane only, no capture) may terminate a failure path
  only when it is narrowed: a narrow catch type (Java/Python) or an
  additional condition inside the branch (Go/JS). An unconditional
  notify in a broad catch routes every failure to a toast and blinds
  telemetry - a finding. A single path may not both capture and notify
  (error/warn already reach the user, so the pair double-shows). A
  `notify` is validated like a capture: static-literal msg, well-formed
  literal dedupKey.
- A skipped test must state a reason: `t.Skip("why")` / `t.Skipf`, or
  `// test-skip: <reason>` above a bare `t.SkipNow()`. The same
  contract holds in every language (skip / todo / xfail /
  `@Disabled`); focused tests (`it.only`, `fit`) are an unconditional
  error.

The same model is enforced beyond Go:

- **Java** (`javalint`, JV001-010) on a typed javaparser AST: JV001
  swallow (every catch path must propagate, report, print, or carry
  `// no-report`), JV002 chain (a thrown exception must carry the
  caught as its cause), JV003 throwable (a catch of `Throwable` /
  `Error` must rethrow), JV004 useless-catch (a catch that only
  rethrows the caught unchanged - deleted, not annotated), JV005 exit
  (`System.exit` in a catch needs a preceding capture; port of ERC003),
  JV006 double-capture (no path may both report and rethrow; port of
  ERC005 - and no path may both capture and notify), JV007 skip
  (`@Disabled` / `@Ignore` must carry a non-empty reason string), JV009
  notify gate (a notify in a broad catch must narrow the type), and
  JV010 reporter args (a Report user-lane verb needs a static-literal
  msg and a well-formed literal dedupKey). JV008 is retired.
- **Python** exception and test-skip rules ship as the `pyrules`
  flake8 plugin (`TBX` codes). A skip reason is accepted in any of
  the natural forms: `@pytest.mark.skip(reason=...)`,
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
- **JS / TS / Svelte** swallow and test-skip rules run under ESLint.
  A skip reason is accepted in the call itself: node:test options
  (`{ skip: 'reason' }` / `{ todo: 'reason' }`) and Playwright's
  `test.skip(cond, 'reason')` / `test.fixme(cond, 'reason')`. The
  notify gate is `no-broad-notify` (a notify must sit under a condition
  inside the catch); `valid-error-report` and `valid-dedup-key` also
  validate `notify`'s msg and dedupKey.

### Python rules (TBX001-011)

The `pyrules` flake8 plugin emits these codes; each maps to a stable
rule id (parity with the pre-migration ids).

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

Full ids carry the `python-` prefix (e.g. `python-swallowed-exception`).
TBX009 is retired (the removed secret-name heuristic, D001), as JV008 is.

### Duplication (DUP001, DUP002)

The `tackbox-jscpd` engine wraps a copy/paste detector and runs by
default over Go, Python, Java, and the JS family (`.js`, `.jsx`, `.mjs`,
`.cjs`, `.ts`, `.tsx`, `.svelte`); Markdown is excluded, since prose
repetition is not a defect. A consumer on `@latest` gets it in CI with
no wiring.

- **DUP001** flags a duplicated block - a clone of at least 50 tokens.
  Both ends are reported, each a finding at its own site, naming the
  counterpart block and the token count.
- **DUP002** flags a native `jscpd:ignore` marker. That channel would
  bypass the gated suppression below, so its presence alone is a
  finding; remove it.

Suppress one clone with a standalone `// dup-ok: <reason>` comment
directly above the block - a `#` or a single-line `/* ... */` comment
works per language. The reason must be at least 10 characters (D009),
and a trailing comment after code does not count. `dup-ok` above one
end drops only that end; above both ends it drops the whole clone.

Duplication is cross-file, so the engine is never cached: it runs on
every lint and writes no clean-cache markers. A `java`-format clone that
lies entirely within both files' headers (package, imports, leading
comments) has no extractable code and is dropped before it is reported.

### Markdown: ASCII and the language marker

The Markdown engine runs the standard markdownlint rules plus `MD-ASCII`,
which flags any non-ASCII character (any codepoint above U+007F) - em
dashes, curly quotes, other scripts, emoji - keeping docs portable and
grep-friendly.

One HTML comment on one of the first five lines widens the alphabet for
a single file:

```text
<!-- tackbox: lang=ru personal experimental repo -->
```

It widens the allowed set to that language's script plus its typographic
punctuation - `ru` today: Cyrillic, guillemets, em/en dash, ellipsis,
curly quotes, NBSP - and nothing else: every other non-ASCII character,
emoji and other scripts included, is still flagged.

The marker is single-use and never disables the rule. A second marker, a
marker past the fifth line, a missing code, or an unknown language code
is itself a finding and leaves the whole file strict ASCII. Any text
after the code (as above) is a free-form note.

## No configuration

By design, the ruleset is a single non-negotiable bundle. There are
no flags to disable individual rules. Suppressing a finding requires
the explicit per-site marker (`// no-report`, `// parse-skip`,
`// nil-return`, `// test-skip`, `// dup-ok`) with a reason of at
least 10 characters - non-empty was too cheap (`ok` / `todo` passed) -
plus a covering line in the approval manifest below: the reason
explains the exception, the manifest line records its approval.

Capture helpers are recognized by origin, not by name: a Go call
counts only when its callee resolves (type info / import) to the
`github.com/nikitatsym/tackbox/go/report` package, a JS/TS call to
`tackbox/report`, and a Java capture when the caught reaches a
`nl.tsym.tackbox.report.Report` call or a known logger sink (e.g.
slf4j, `java.lang.System.Logger`) at `ERROR` / `WARNING` - tier-1.
Every language also honors a function declared in a repo-root
`.tackbox-reporters` file (`file#function: reason`) - tier-2. A
declaration names a report sink - it is not an exclude: it disables no
rule, and a declared call is honored only when the caught error flows
into its arguments. Python resolves tier-1 by import origin too (D010),
scoped to the fixed `tackbox_report` package (`report_error` /
`report_warn` / `report_quiet` / `report_panic` / `notify`): a call
counts only when it resolves through the module's own import bindings -
`from tackbox_report import ...` or `import tackbox_report` (attribute
form included) - so a same-named local def or a foreign import is not
the verb. Only its tier-2 declarations stay matched by function name
(any same-named call), not by resolving the callee to its file.

A `[usage]` declaration (`file#function [usage]: reason`) names the
opposite lane: a deliberate user-facing diagnostic exit, e.g. a CLI
`usage()` helper. It is never a capture. Its calls are clean outside
err-branches (nothing failed - no marker needed) and a finding inside
one (wrong sink for a failure path), regardless of arguments. Only
erclint (ERC003) consumes usage sinks today, so a `[usage]` declaration
on a non-Go file is rejected - a dead line would be silent. The format
is language-uniform; the restriction lifts as other engines adopt the
contract.

## Approval manifest

Suppression markers are approved in one committed file,
`.tackbox/approvals` at the repo root - one line per approved
occurrence: an address (file plus named-scope chain) and the exact
marker text.

```text
py/app/svc.py#Handler.process: no-report: legacy path, covered upstream
js/src/boot.ts#init.<h4f2a9c1e>: no-report: splash fallback, reported upstream
tools/gen.py: parse-skip: config validated upstream
```

The chain walks functions, classes, methods, or Markdown headings,
joined by `.`; an entry with no `#` sits at file scope. Anonymous
scopes (lambdas, arrows, IIFEs) appear as 8-hex content hashes; Java
overloads carry a parameter-type signature; same-name siblings take
an `@k` ordinal. Repeat the line for each identical occurrence.

The check is bidirectional and always covers the whole tree: a
marker without a covering entry and an entry without a live marker
(an orphan) are both findings, reported by `tackbox lint` under the
`approvals (whole tree):` header whatever the lint scope.
`tackbox approvals` runs the same check standalone;
`tackbox approvals --draft` prints a ready entry line for every
uncovered marker - the address is computed for you, so approving a
marker you just wrote is one append away, and bootstrapping a repo
that already carries markers is: generate, review line by line,
commit.

Approving is adding the line. In an agent session the edit that adds
a manifest line draws the PreToolUse ask quoting the entry (several
lines in one edit draw one all-or-nothing ask), so the only route to
a green check passes through a visible diff and a human decision.
Writing a marker itself never asks - by any channel, Edit or shell -
it merely leaves the tree inconsistent, which every later hook
event, `dev.py check`, and CI reports until the entry lands or the
marker is reverted. Removing a manifest line is free; a marker whose
text, scope, or count changes needs its entry updated the same way.

## Runtime reporting helpers

Direct reporting helpers ship per language; their shared runtime behavior -
lane routing, telemetry dedup, panic grouping, and capture isolation - is
specified in [docs/report-contracts.md](docs/report-contracts.md).

- [Go](go/report/README.md)
- [JavaScript / TypeScript](js/README.md)
- [Python](py/tackbox_report/README.md)
- [Java](java/report/README.md)

## Agent hook (Claude Code)

`tackbox hook` wires the rules into an agent's edit loop. It reads a
Claude Code hook event on stdin and dispatches by `hook_event_name`:

- **PostToolUse** on an Edit/Write re-lints the edited file (Go: its
  package). On a finding it exits 2 with the finding on stderr, so
  the model sees it and fixes it in-loop. Every Post event - **Bash**
  included - also runs the whole-tree approvals consistency check:
  an unapproved marker, an orphaned entry, or an unresolvable file
  blocks with the entry named and the fix - add the manifest line,
  which asks, or revert. Stateless and tree-shaped: a commit changes
  nothing, and the block repeats on every event until the tree is
  consistent. The authoritative gate stays pre-commit / CI.
- **PreToolUse** asks for approval before a new `.tackbox/approvals`
  line or a new `.tackbox-reporters` line lands; removing one is
  free. Editing markers in code draws no Pre ask - the consistency
  check owns them.

Only markers in files an engine would lint participate in the check
(D012): a marker in a Go `testdata/` path or a non-lintable fixture
extension (a `.java.txt`) is dead text - no entry needed, no
question - while the `.tackbox-reporters` gate stays unconditional.

The hook is a no-op unless the edit's `cwd` is a git repo with a
`dev.py` at its root. Wire it once, globally, in
`~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "Edit|Write|MultiEdit",
       "hooks": [{"type": "command", "command": "uvx tackbox hook"}]}
    ],
    "PostToolUse": [
      {"matcher": "Edit|Write|MultiEdit|Bash",
       "hooks": [{"type": "command", "command": "uvx tackbox hook", "timeout": 120}]}
    ]
  }
}
```

`uvx tackbox hook` runs the cached tackbox (no `@latest`): the hook is
fast in-loop feedback, not the authoritative gate.

## Escapes inventory

`tackbox escapes` prints the repo's whole bypass surface as JSON on
stdout - every place code legitimately steps off the paved road, in one
cheap command that review tooling of any harness can consume (D013). It
enumerates:

- **suppression markers** (`// no-report`, `// parse-skip`,
  `// nil-return`, `// long-comment`, `// test-skip`, `// dup-ok`, plus
  the markdown `tackbox: lang=` marker), each with its reason;
- **`.tackbox-reporters` declarations** - the tier-2 sinks;
- **notify / quiet lane choices** - the call sites of the user-lane-only
  `notify` and the telemetry-only `quiet` verbs.

It is an **inventory, not a gate**: it exits 0 whenever it runs, entries
or not, and is not wired into `dev.py check`. The rules and the hook are
the enforcement; this command is food for a reviewer (human or agent)
who wants the escapes laid out without re-deriving them. Exit is nonzero
(1, one stderr line) only for an infra error - a bad `--since` rev.

```bash
uvx tackbox@latest escapes
uvx tackbox@latest escapes --since origin/main --context 5
```

### JSON contract

```json
{
  "version": 1,
  "since": null,
  "entries": [
    {"kind": "marker", "file": "a/b.py", "line": 12,
     "text": "no-report: central boundary already captures it",
     "reason": "central boundary already captures it",
     "context": ["...", "...", "..."]},
    {"kind": "reporter-decl", "file": ".tackbox-reporters", "line": 2,
     "text": "src/app/errors.py#report_api_error: the API sink",
     "context": ["..."]},
    {"kind": "notify-site", "file": "js/foo.js", "line": 40,
     "text": "notify('offline', err, {}, 'net.offline')",
     "context": ["..."]},
    {"kind": "quiet-site", "file": "go/x.go", "line": 9,
     "text": "report.Quiet(ctx, ...)", "context": ["..."]}
  ],
  "counts": {"marker": 1, "reporter-decl": 1, "notify-site": 1, "quiet-site": 1}
}
```

- `version` is the schema version (`1`); `counts` always carries all four
  kinds, even at zero, so consumers see a stable shape.
- `since` echoes the `--since` rev, or `null`.
- `text` is the trimmed source line; for a marker it runs from the marker
  keyword to end of line.
- `reason` (markers only) is what follows the keyword's colon, trimmed -
  possibly empty (the `tackbox: lang=` marker carries none).
- `context` is the surrounding source, `--context N` lines each side
  (default 3), inclusive of the entry line itself - the window
  `[line-N, line+N]`, clipped at file edges, each line trimmed of trailing
  whitespace. It is plain source; the entry line is not marked.
- `entries` are sorted by `(file, line)` for stable output.

### Scope and detection

The scan covers the same lintable source set the linter would scan (the
D012 predicate: extension match plus each engine's path filter, so a Go
`testdata/` file is out), plus the root `.tackbox-reporters` (every
non-empty line is one declaration - the file has no comment syntax).
notify / quiet call sites are detected **textually per language**
(`report_quiet` / `notify` in Python, `reportQuiet` / `notify` in the JS
family, `.Quiet(` / `.Notify(` in Go, `.quiet(` / `.notify(` in Java),
word-boundaried so `notifyAll(` does not match. Textual detection can
over-report (a match inside a comment or string counts) - that is fine:
this is observability, not a lint.

### `--since <rev>`

`--since <rev>` prints only entries **new against `<rev>`**, compared by
content identity `(kind, file, text)` - the same extraction run against
the tree at `<rev>` (via `git ls-tree` + `git show`) subtracted, count
aware, from the current tree's entries. It over-reports on moved code (a
new file path is a new identity) but never silently drops an entry - the
conservative direction for a review aid. A bad rev is the one infra error:
one stderr line, exit 1.

## Layout

```text
.tackbox/approvals                     # suppression-approval manifest
dev.py                                 # lint / test / e2e / check (dev-script)
hygiene.py                             # dev.py lint hygiene (conflict/yaml/ws/newline)
go.mod                                 # Go module
package.json                           # npm package (ESLint plugin + report helper)
eslint.config.preset.js                # default config used by tackbox-eslint bin
bin/tackbox-eslint.js                  # ESLint CLI wrapper with bundled preset
bin/tackbox-mdlint.js                  # markdownlint wrapper with bundled preset
go/
  cmd/erclint/                         # native Go analyzers (ERC001-009)
  cmd/erclint-opengrep/                # opengrep wrapper, embedded rule yamls
    rules/                             # exceptions-go (go-exit-in-recover)
  analyzers/                           # per-rule go/analysis packages
  internal/                            # markers + AST helpers
  report/                              # Go capture helper (Sentry/glitchtip)
java/
  pom.xml                              # Maven module -> shaded javalint.jar
  src/main/.../javalint/               # typed-AST analyzer (JV001-010)
    rules/                             # per-rule checkers
  report/                              # Java capture helper -> Maven Central io.github.nikitatsym:report
js/
  eslint-plugin.js                     # ESLint plugin entry
  rules/                               # 14 frontend rules
  markdownlint-rules/                  # custom markdownlint rules
  report.js                            # browser capture helper (@sentry/browser)
  tests/                               # RuleTester + node:test
py/
  tackbox/                             # lint / hook / doctor CLI, cache, engines
    pyrules/                           # flake8 TBX plugin (python exception rules)
  tackbox_report/                      # Python capture helper -> PyPI tackbox-report
  tests/                               # pytest suite
docs/
  publishing-helpers.md                # helper release runbook (PyPI + Maven Central)
```

## Repo conventions

- Versioned via git tags (`vMAJOR.MINOR.PATCH`); CI auto-bumps the
  patch tag on every green push to `main` and publishes the wheels.
  Consumers track `@latest`, never a pinned version.

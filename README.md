# tackbox

![tackbox logo](assets/logo-round.png)

[![publish](https://github.com/nikitatsym/tackbox/actions/workflows/publish.yml/badge.svg)](https://github.com/nikitatsym/tackbox/actions/workflows/publish.yml)
[![verify-release](https://github.com/nikitatsym/tackbox/actions/workflows/verify-release.yml/badge.svg)](https://github.com/nikitatsym/tackbox/actions/workflows/verify-release.yml)
[![pypi](https://raw.githubusercontent.com/nikitatsym/tackbox/badges/pypi.svg)](https://pypi.org/project/tackbox/)

**Every failure must report, propagate, or explain itself.**

Coding agents write error handling that looks right and silently
isn't: a swallowed exception, a fatal exit with nothing logged, a
report with the cause stripped out. tackbox catches it the moment
it's written: hooked into the agent's edit loop it flags the finding
before the turn ends, and the same rules gate pre-commit and CI -
one coverage bar for hand-written and agent-written code.

And there is no quiet way around any of it: no flags, no config. The
only escape is an explicit `// no-report: <reason>` at the site - and
the agent hook asks for your approval before a new suppression lands.

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
macOS x86_64/arm64, and Windows x86_64. `engines.json` in the thin
wheel records the source, version, sha256, and license of every
bundled binary and dependency; `tackbox doctor` fetches the store if
absent and verifies the payload against it.

## What the rules enforce

Covers ERC001-009 (Go, via `erclint`), JV001-010 (Java, via the native
`javalint` engine; JV008 is retired), Python exception, notify, and
test-skip rules (via the `pyrules` flake8 plugin), frontend swallow,
notify, and test-skip rules (JS, TS, Svelte, via ESLint), and Markdown
(MD001-060 + ASCII).

See `go/README.md` for the Go ruleset. The specs these rules implement
(`error-reporting-and-coverage`, `error-handling-frontend`) live
outside this repo (private notes); the public summary:

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
- Capture-call arguments must not carry raw user input, and the
  dedupKey must be a well-formed literal.
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
  `dedup_key` (TBX011) - are recognized by name (the D004 caveat).
- **JS / TS / Svelte** swallow and test-skip rules run under ESLint.
  A skip reason is accepted in the call itself: node:test options
  (`{ skip: 'reason' }` / `{ todo: 'reason' }`) and Playwright's
  `test.skip(cond, 'reason')` / `test.fixme(cond, 'reason')`. The
  notify gate is `no-broad-notify` (a notify must sit under a condition
  inside the catch); `valid-error-report` and `valid-dedup-key` also
  validate `notify`'s msg and dedupKey.

## No configuration

By design, the ruleset is a single non-negotiable bundle. There are
no flags to disable individual rules. Suppressing a finding requires
the explicit per-site marker (`// no-report`, `// parse-skip`,
`// nil-return`, `// test-skip`, `// dup-ok`) with a reason of at
least 10 characters - non-empty was too cheap (`ok` / `todo` passed).

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
into its arguments. Python is the exception: its flake8/ast engine
resolves no origins - the `tackbox_report` capture functions
(`report_error` / `report_warn` / `report_quiet` / `report_panic`) are a
built-in tier-1 set matched by name, and a tier-2 declaration likewise matches
by function name (any same-named call), not by resolving the callee to
its file.

A `[usage]` declaration (`file#function [usage]: reason`) names the
opposite lane: a deliberate user-facing diagnostic exit, e.g. a CLI
`usage()` helper. It is never a capture. Its calls are clean outside
err-branches (nothing failed - no marker needed) and a finding inside
one (wrong sink for a failure path), regardless of arguments. Only
erclint (ERC003) consumes usage sinks today, so a `[usage]` declaration
on a non-Go file is rejected - a dead line would be silent. The format
is language-uniform; the restriction lifts as other engines adopt the
contract.

## Deduplication: telemetry, never the user

Dedup lives at two levels with different owners (`rules/DECISIONS.md`
D005):

- The capture helpers rate-limit telemetry: repeat captures with the
  same dedupKey inside the rate window (default 60s) are not re-sent.
  Lossless - the server groups by fingerprint and counts repeats.
- The user lane is never suppressed by the helpers. Every user-facing
  event is delivered carrying its dedupKey; collapsing a storm into
  one live banner or a counter is presentation policy and belongs to
  the app's listener, keyed on that dedupKey. A notification dropped
  inside the helper would be a swallowed error at the UI level - the
  exact failure mode tackbox exists to prevent.

## Agent hook (Claude Code)

`tackbox hook` wires the rules into an agent's edit loop. It reads a
Claude Code hook event on stdin and dispatches by `hook_event_name`:

- **PostToolUse** re-lints the edited file (Go: its package). On a
  finding it exits 2 with the finding on stderr, so the model sees it
  and fixes it in-loop. The authoritative gate stays pre-commit / CI.
- **PreToolUse** asks for approval before a new suppression marker
  (`// no-report`, `// parse-skip`, `// nil-return`, `// test-skip`,
  `// dup-ok`) or a new `.tackbox-reporters` line lands;
  removing one is free.

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
      {"matcher": "Edit|Write|MultiEdit",
       "hooks": [{"type": "command", "command": "uvx tackbox hook", "timeout": 120}]}
    ]
  }
}
```

`uvx tackbox hook` runs the cached tackbox (no `@latest`): the hook is
fast in-loop feedback, not the authoritative gate.

## Layout

```text
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

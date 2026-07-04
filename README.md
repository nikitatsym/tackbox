# tackbox

[![publish](https://github.com/nikitatsym/tackbox/actions/workflows/publish.yml/badge.svg)](https://github.com/nikitatsym/tackbox/actions/workflows/publish.yml)
[![verify-release](https://github.com/nikitatsym/tackbox/actions/workflows/verify-release.yml/badge.svg)](https://github.com/nikitatsym/tackbox/actions/workflows/verify-release.yml)
[![pypi](https://raw.githubusercontent.com/nikitatsym/tackbox/badges/pypi.svg)](https://pypi.org/project/tackbox/)

Universal lint rules that enforce the `error-reporting-and-coverage`
and `error-handling-frontend` specs across Go, Python, JS, TS, and
Svelte. One command brings the whole enforcement stack - no
`go install`, no `npm i`, no external `opengrep`:

```bash
uvx tackbox@latest lint .
```

The wheel is hermetic: a consumer needs only `git` on PATH (plus a Go
toolchain if the repo has `.go` files). Rules roll out via `@latest` -
a new safety rule reaches every repo on its next run.

Covers ERC001-007 (Go, via `erclint`), ERC006 fingerprint rules (Go,
Python, JS, TS, via the `opengrep` wrapper), frontend swallow rules
(JS, TS, Svelte, via ESLint), and Markdown (MD001-059 + ASCII).

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

## Quick start (Go CLI, no pre-commit)

```bash
go install github.com/nikitatsym/tackbox/go/cmd/erclint@latest
erclint ./...
```

## Quick start (opengrep wrapper, no pre-commit)

```bash
go install github.com/nikitatsym/tackbox/go/cmd/erclint-opengrep@latest
erclint-opengrep path/to/sources
```

`erclint-opengrep` is a thin Go wrapper: it embeds the rule yamls
and shells out to `opengrep scan`. Opengrep itself must be on PATH
(install from <https://github.com/opengrep/opengrep/releases> or via
Homebrew).

## What the rules enforce

See `go/README.md` for the Go ruleset. The spec these rules implement
lives outside this repo (private notes); the public summary:

- Every `err != nil` branch must propagate, capture, or carry an
  explicit `// no-sentry: <reason>` marker.
- Common parser results that fall through to `nil` must capture or
  carry `// parse-skip: <reason>`.
- Terminal exits (`log.Fatal*`, `os.Exit`, project-local `die`) must
  be preceded by a capture call or carry a `// no-sentry: <reason>`
  marker (e.g. for the normal `os.Exit(0)` at the end of main).
- Bare `return nil` from a single-result function must carry
  `// nil-return: <reason>` or use `(val, ok)` / `(val, err)`.
- A single err-branch may not both capture and `return err`.
- Fingerprint arguments must not reference secret-named identifiers
  or raw user input.

## No configuration

By design, the ruleset is a single non-negotiable bundle. There are
no flags to disable individual rules. Suppressing a finding requires
the explicit per-site marker (`// no-sentry`, `// parse-skip`,
`// nil-return`) with a non-empty reason.

Capture helpers are recognized by origin, not by name: a call counts
only when its callee resolves (type info / import) to the
`github.com/nikitatsym/tackbox/go/report` (Go) or `tackbox/report`
(JS/TS) package, or to a function declared in a repo-root
`.tackbox-reporters` file (`file#function: reason`). A declaration
names a report sink - it is not an exclude: it disables no rule, and a
declared call is honored only when the caught error flows into its
arguments.

## Agent hook (Claude Code)

`tackbox hook` wires the rules into an agent's edit loop. It reads a
Claude Code hook event on stdin and dispatches by `hook_event_name`:

- **PostToolUse** re-lints the edited file (Go: its package). On a
  finding it exits 2 with the finding on stderr, so the model sees it
  and fixes it in-loop. The authoritative gate stays pre-commit / CI.
- **PreToolUse** asks for approval before a new suppression marker
  (`// no-sentry`, `// parse-skip`, `// nil-return`, `// long-comment`)
  or a new `.tackbox-reporters` line lands; removing one is free.

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
go.mod                                 # Go module
package.json                           # npm package (ESLint plugin + report helper)
eslint.config.preset.js                # default config used by tackbox-eslint bin
bin/tackbox-eslint.js                  # ESLint CLI wrapper with bundled preset
bin/tackbox-mdlint.js                  # markdownlint wrapper with bundled preset
go/
  cmd/erclint/                         # native Go analyzers (ERC001-005)
  cmd/erclint-opengrep/                # opengrep wrapper, embedded rule yamls
    rules/                             # multi-language ERC006 yamls
  analyzers/                           # per-rule go/analysis packages
  internal/                            # markers + AST helpers
  report/                              # Go capture helper (Sentry/glitchtip)
js/
  eslint-plugin.js                     # ESLint plugin entry
  rules/                               # 8 frontend rules
  markdownlint-rules/                  # custom markdownlint rules
  report.js                            # browser capture helper (@sentry/browser)
  tests/                               # RuleTester + node:test
py/
  tackbox/                             # lint / hook / doctor CLI, cache, engines
  tests/                               # pytest suite
```

A Java analyzer directory with its own manifest will be added in a
later version, next to `go.mod`, `package.json`, and `py/`.

## Repo conventions

- Versioned via git tags (`vMAJOR.MINOR.PATCH`). Consumers pin `rev`.

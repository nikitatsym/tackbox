# tackbox

[![publish](https://github.com/nikitatsym/tackbox/actions/workflows/publish.yml/badge.svg)](https://github.com/nikitatsym/tackbox/actions/workflows/publish.yml)
[![verify-release](https://github.com/nikitatsym/tackbox/actions/workflows/verify-release.yml/badge.svg)](https://github.com/nikitatsym/tackbox/actions/workflows/verify-release.yml)
[![pypi](https://img.shields.io/pypi/v/tackbox)](https://pypi.org/project/tackbox/)

Universal lint rules that enforce the `error-reporting-and-coverage`
spec across languages. Drop the repo into `.pre-commit-config.yaml`
and the rules apply uniformly.

## Hooks

| Hook id            | Engine       | Languages          | Rules covered     |
|--------------------|--------------|--------------------|-------------------|
| `erclint-go`       | go/analysis  | Go                 | ERC001-005        |
| `erclint-opengrep` | opengrep     | Go, Python, JS, TS | ERC006            |
| `tackbox-eslint`   | ESLint       | JS, TS, Svelte     | frontend swallow  |
| `tackbox-mdlint`   | markdownlint | Markdown           | MD001-059 + ASCII |

Per-language hooks for Python and Java analyzers come in later
versions.

### Prerequisites

- Go 1.24+ in PATH (pre-commit installs `erclint` and
  `erclint-opengrep` via `go install`).
- `opengrep` binary in PATH for the `erclint-opengrep` hook. See
  <https://github.com/opengrep/opengrep> for installation.
- Node 20+ in PATH for the `tackbox-eslint` and `tackbox-mdlint`
  hooks.

## Quick start (pre-commit)

```yaml
# .pre-commit-config.yaml in the consumer repo
repos:
  - repo: https://github.com/nikitatsym/tackbox
    rev: main  # rolling release
    hooks:
      - id: erclint-go
      - id: erclint-opengrep
      - id: tackbox-eslint
      - id: tackbox-mdlint
```

Then:

```bash
pre-commit install
pre-commit run --all-files
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

## Layout

```text
.pre-commit-hooks.yaml                 # hooks exposed to consumers
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
```

Python and Java directories with their own manifests will be added
in later versions; they will sit next to `go.mod` and `package.json`
in the repo root.

## Repo conventions

- Versioned via git tags (`vMAJOR.MINOR.PATCH`). Consumers pin `rev`.

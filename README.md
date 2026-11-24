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

There are no per-rule disable flags: a local exception stays visible as a
reasoned marker at the site and needs an approval (see
[Exceptions](#exceptions)).

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
- **Unused bindings** - unused imports and locals, trailing JS/TS/Svelte
  arguments, and dead private declarations. Required unused bindings follow
  the [language conventions](rules/DECISIONS.md#d021---unused-bindings-are-a-mandatory-lane-2026-10-10).

## Requirements

tackbox runs through `uvx` (part of [uv](https://docs.astral.sh/uv/)) inside a
Git checkout. The wheel is hermetic: beyond that, a consumer needs only `git`
on PATH (plus a Go toolchain if the repo has `.go` files, and a Java 17+
runtime if it has `.java` files). Rules roll out via `@latest` - a new safety
rule reaches every repo on its next run.

The first run of each engine payload version downloads it once into a per-user
store under `$XDG_DATA_HOME/tackbox/` (default `~/.local/share/tackbox/`);
later runs reuse it without downloading again, including later tackbox
versions that pin the same payload. To skip the download, point
`TACKBOX_ENGINES_DIR` / `TACKBOX_OPENGREP_DIR` at an already-unpacked payload.
How payloads are pinned and verified: [docs/distribution.md](docs/distribution.md).

Wheels cover Linux x86_64/arm64 (manylinux), macOS 15 or newer on arm64,
Windows x86_64, and Android arm64 (`android_24_arm64_v8a`, Termux). Android
also needs Termux Python 3.13+ and a system Node.js matching
`^20.19.0 || ^22.13.0 || >=24.0.0`:

```bash
pkg install python uv git nodejs-lts   # plus golang / openjdk-17 for .go / .java
```

Third-party license texts and dependency inventories ship in the jar under
`META-INF/third-party` and in the wheel under `tackbox/third_party/licenses`;
see the [engine notices](engines/licenses/NOTICE.txt).
PMD acknowledgment: This product includes software developed in part by
support from the Defense Advanced Research Project Agency (DARPA).

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

`tackbox lint --codequality <path>` also writes a CodeClimate-format JSON
array of every finding to `<path>` (console output and exit code unchanged).
A failed engine scope adds one `tackbox-engine-error` issue with `critical`
severity. Wire it into GitLab CI as a `codequality` report:

```yaml
lint:
  script: uvx tackbox@latest lint . --codequality gl-code-quality.json
  artifacts:
    reports:
      codequality: gl-code-quality.json
```

## Commands

- `tackbox lint [path]` - `0` clean, `1` findings, `2` a scope matching no
  files, an unusable `--changed` / `--since` (an unknown ref, or a repo with
  no commits), or an engine or setup failure. A closed downstream pipe
  (`lint | head`) exits `141`.
- `tackbox approvals` - check the approval manifest against the tree: `0`
  consistent, `2` inconsistent (uncovered markers, orphaned entries, or
  unresolvable files), `1` infra.
- `tackbox approvals --draft` - print an entry line for every uncovered marker.
  Not a gate: `0` when the draft is complete, orphans or not; `2` when
  unresolvable files leave it incomplete; `1` infra.
- `tackbox escapes [--since <rev>]` - print the bypass surface as JSON
  ([schema](docs/escapes.md)). Not a gate: `0` whenever it runs, entries or
  not; `1` for an infrastructure failure, such as attributes that cannot be
  resolved, a bad `--since` rev, or git older than 2.40 on the `--since` path.
- `tackbox doctor` - verify the install: `0` all checks pass, `1` at least
  one failed.
- `tackbox hook`, `tackbox hook-protocol` - the [agent hook](#agent-hook);
  exit codes in [docs/agent-hook.md](docs/agent-hook.md#exit-codes).

`tackbox lint` scans the source set - tracked files plus untracked files that
are not ignored - under `path` (default `.`). `--changed` narrows it to files
that differ from HEAD (staged, unstaged, untracked); `--since <ref>` to the
three-dot diff `<ref>...HEAD` plus those, so it already includes `--changed`.
`path` composes with both: `tackbox lint src --changed` lints only the dirty
files under `src/`. The approvals check ignores the scope: it always covers
the whole tree, so a scoped CI lint cannot scope the wall away.

## Rules

Covers ERC001-011 (Go, via `erclint`), JV001-007 and JV009-014 (Java, via
`javalint`), Python exception, reporting, test, and unused-binding rules
(via flake8), frontend rules (JS, TS, Svelte, via ESLint), duplication,
and Markdown link integrity and declared-charset checks. References:
[Go](go/README.md), [Java](java/README.md), [Python](py/README.md),
[JS / TS / Svelte](js/README.md#rules), [Markdown](js/README.md#markdown),
and the [cross-language rules](docs/rules.md) (core policy, duplication,
reporter declarations). [rules/DECISIONS.md](rules/DECISIONS.md) records every
rule decision and its rationale.

## Exceptions

There are no flags to disable individual rules. To suppress a finding, put a
marker with its reason at the site, such as `// no-report: <reason>`
([keywords and forms](docs/suppression.md#markers)), and approve it with a line
in the approval manifest. The unused-binding lane has no suppression.

### Approval manifest

`.tackbox/approvals` at the repo root holds one line per approved occurrence:
an address (file plus named-scope chain) and the exact marker text.

```text
py/app/svc.py#Handler.process: no-report: legacy path, covered upstream
js/src/boot.ts#init.<h4f2a9c1e>: no-report: splash fallback, reported upstream
tools/gen.py: parse-skip: config validated upstream
```

Approving is adding the line (`tackbox approvals --draft` prints it, see
[Commands](#commands)); in an agent session the edit that adds it draws an
approval ask. Address grammar and the check:
[docs/suppression.md](docs/suppression.md#approval-manifest).

### Generated and vendored code

A file whose `.gitattributes` sets `linguist-generated`, `gitlab-generated`,
or `linguist-vendored` is excluded from the whole lint:

```text
gen/**       linguist-generated
vendor/**    linguist-vendored
gen/keep.go  linguist-generated=false
```

Use `dir/**`, not `dir/`: gitattributes patterns, unlike gitignore, do not
match a trailing-slash directory form. Details:
[docs/suppression.md](docs/suppression.md#generated-and-vendored-code).

### Escapes inventory

`tackbox escapes` lists every marker, `.tackbox/reporters` declaration,
notify / quiet call site, and attribute-excluded file for review
([schema](docs/escapes.md)).

## Runtime reporting helpers

Direct reporting helpers ship per language; their shared runtime behavior -
lane routing, telemetry dedup, panic grouping, and capture isolation - is
specified in [docs/report-contracts.md](docs/report-contracts.md).

- [Go](go/report/README.md)
- [JavaScript / TypeScript](js/README.md)
- [Python](py/tackbox_report/README.md)
- [Java](java/report/README.md)

## Agent hook

The same rules run in a coding agent's edit loop. Before an edit, gated
changes (a new approval or reporter line, an exclusion in `.gitattributes`, an
edit of an excluded file, removing the root `dev.py`) ask for approval; after
it, findings on the added lines come back as a tool error. The hook never
undoes a write, and shell commands run no approval checks. Contract:
[docs/agent-hook.md](docs/agent-hook.md).

Every host needs `uvx` on PATH: the hook runs tackbox through it. Codex and
Oh My Pi also need npm and a Node.js matching
`>=20.19.0 <22.0.0 || >=22.12.0 <23.0.0 || >=23.2.0` for the adapter. The
adapter pins the Python wheel to its own version (`uvx tackbox@<version>`);
if that wheel is not cached yet, the next edit downloads it, so it needs
network access then.

### Claude Code

`tackbox hook` reads a Claude Code hook event on stdin and dispatches by
`hook_event_name` (`PreToolUse`, `PostToolUse`). Wire it once, globally, in
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

`uvx tackbox hook` runs the cached tackbox (no `@latest`), so the in-loop
hook does not wait for a registry lookup.

### Codex

Install the adapter once; `tackbox-codex-hook` must be on the PATH Codex runs
hooks with:

```bash
npm install --global tackbox@latest
```

Then wire `tackbox-codex-hook` for `apply_patch` in `~/.codex/hooks.json`
([snippet](docs/agent-hook.md#codex)). Codex has no `ask` decision, so an
approval-gated edit blocks with the reason; make that change in a host that
supports the prompt.

### Oh My Pi

```bash
omp plugin install tackbox
```

That is the whole wiring. Approval dialogs, headless sessions, and host
limits: [docs/agent-hook.md](docs/agent-hook.md#oh-my-pi).

## Development

Working on tackbox itself - toolchains, the `dev.py check` gate, releases:
[CONTRIBUTING.md](CONTRIBUTING.md).

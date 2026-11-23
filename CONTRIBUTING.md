# Contributing to tackbox

## Gate

Run `pre-commit install` (or `uvx pre-commit install`) once per checkout.
The hook in `.pre-commit-config.yaml` runs `python3 dev.py check` on every
commit, and CI runs the identical gate.

The following host toolchains must be on `PATH`:

- Go, pinned by `go.mod`;
- Node 22 with npm; CI's Node version is in `.github/workflows/ci.yml`, while
  the bundled engine's Node version is pinned in `engines/manifest.json`;
- JDK 17 with Maven; the Java level and Maven configuration live in
  `java/pom.xml`;
- opengrep, pinned in `engines/manifest.json` and installed at that version
  by CI;
- uv, which is not version-pinned.

Everything else - `node_modules`, Maven build output, Python virtual
environments, and Go modules - is reproduced by `dev.py check`; nothing needs
to be synchronized by hand.

`dev.py check` builds the host's wheels only. Building the Android wheels
(`python scripts/build_wheels.py --platform android-aarch64`) additionally needs
a linux-x86_64 host with rustup on `PATH`; the script installs the pinned Rust
toolchain and fetches the pinned NDK itself.

## Releases

tackbox is versioned via git tags (`vMAJOR.MINOR.PATCH`); CI auto-bumps the
patch tag on every green push to `main` and publishes the wheels.
Consumers track `@latest`, never a pinned version.

A tagged wheel is built and protocol-canary tested, published to PyPI, then a
successful release workflow automatically publishes the matching npm package
from its immutable completed-run source. If a pending npm job is canceled,
rerun `publish` from the Actions UI; there is no standalone npm redispatch.

The runtime helper libraries release on their own tags; see
[docs/publishing-helpers.md](docs/publishing-helpers.md).

## Oh My Pi against a working tree

For development against a working tree, name the command explicitly - a JSON array
of argv, never a shell string:

```bash
TACKBOX_OMP_COMMAND='["uv","run","--directory","/absolute/path/to/tackbox/py","python","-m","tackbox.cli"]'
```

The subcommand is appended by the extension, never taken from the override, so
a development command cannot answer a different protocol. There is no
`@latest` fallback: an unpinned wheel could answer a protocol version the
extension does not speak.

## Rule changes

Read [rules/DECISIONS.md](rules/DECISIONS.md) before changing a rule: it
records every rule decision and its rationale.

## Layout

```text
.tackbox/    this repo's approvals manifest and reporter declarations
assets/      logo and social preview
bin/         npm bin wrappers: tackbox-eslint, tackbox-mdlint, tackbox-codex-hook
docs/        user reference and the helper release runbook
engines/     payload wheel projects (tackbox-engines, tackbox-opengrep), pins, notices
go/          erclint analyzers and the Go report helper
java/        javalint and the Java report helper
js/          ESLint plugin, markdownlint rules, report helpers, OMP and Codex adapters
py/          tackbox CLI, pyrules, tests, and the Python report helper
rules/       rule decision record
scripts/     wheel build, payload lock and publish guard, e2e fixture, Termux smoke
dev.py       the gate: lint / test / check
hygiene.py   dev.py lint hygiene (conflict markers, yaml, whitespace, newlines)
```

# Contributing to tackbox

## Gate

Run `pre-commit install` (or `uvx pre-commit install`) once per checkout.
The hook in `.pre-commit-config.yaml` runs `python3 dev.py check` on every
commit, and CI runs the identical gate. The gate is [dev.py](dev.py), with
text hygiene in [hygiene.py](hygiene.py).

The following host toolchains must be on `PATH`:

- Go, at the version `go.mod` requires;
- Node.js with npm, at the major version `.github/workflows/ci.yml` sets up
  (the bundled engine's Node is pinned separately in `engines/manifest.json`);
- a JDK with Maven, at the Java release `java/pom.xml` sets;
- opengrep, at the version pinned in `engines/manifest.json` (`dev.py`
  checks it);
- uv.

Everything else - `node_modules`, Maven build output, Python virtual
environments, and Go modules - is reproduced by `dev.py check`; nothing needs
to be synchronized by hand.

## Releases

Every green push to `main` is a release: CI tags the next patch version
(`vMAJOR.MINOR.PATCH`), publishes the wheels to PyPI after a protocol canary,
and then publishes the npm package of the same version. Batch related changes
before pushing. If a pending npm job is canceled, rerun `publish` from the
Actions UI; there is no standalone npm redispatch.

The release workflows are in [.github/workflows](.github/workflows). They
build wheels with [scripts/build_wheels.py](scripts/build_wheels.py); building
the Android wheels (`--platform android-aarch64`) by hand needs a linux-x86_64
host with rustup on `PATH`, and the script installs the pinned Rust toolchain
and fetches the pinned NDK itself.

The runtime helper libraries release on their own tags; see
[docs/publishing-helpers.md](docs/publishing-helpers.md).

## Oh My Pi against a working tree

For development against a working tree, name the command explicitly - a JSON array
of argv, never a shell string:

```bash
TACKBOX_OMP_COMMAND='["uv","run","--directory","/absolute/path/to/tackbox/py","python","-m","tackbox.cli"]'
```

The extension appends `hook-protocol` itself, so the override names only the
command. Without the override it runs `uvx tackbox@<npm package version>`,
never `@latest`.

## Rule changes

Read [rules/DECISIONS.md](rules/DECISIONS.md) before changing a rule: it
records every rule decision and its rationale.

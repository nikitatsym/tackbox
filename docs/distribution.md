# Distribution

How the `tackbox` wheel gets its engines. Requirements and platforms are in
the [README](../README.md#requirements).

`uvx tackbox@latest` installs one small wheel; the engine payloads are
fetched separately and cached per version:

- `tackbox` (thin) - the Python CLI (including the `pyrules` flake8
  plugin), the `erclint` / `erclint-opengrep` binaries, the
  `javalint.jar`, the opengrep rule yamls, and the ESLint and
  markdownlint plugins and presets. Platform-specific, bumped on every
  push.
- `tackbox-engines` (fat) - the bundled Node runtime (none on Android),
  the `jscpd` binary, and the vendored third-party `node_modules`.
  Version in `engines/VERSION`; bumped only when one of these changes.
- `tackbox-opengrep` - opengrep and its required notices.

Both payload wheels are published to PyPI but are **not** pip
dependencies of thin. The thin wheel's `engines.json` pins each one
exactly (project, version, wheel file name, and the sha256 of its
unpacked tree). On the first run for a given payload version, tackbox
resolves the pinned wheel via the PyPI JSON API, verifies its unpacked
payload against the pinned tree sha256, and unpacks it once into
`$XDG_DATA_HOME/tackbox/engines/<version>/` or
`$XDG_DATA_HOME/tackbox/opengrep/<version>/` (default
`~/.local/share/...`; override with `TACKBOX_ENGINES_DIR` /
`TACKBOX_OPENGREP_DIR`, each naming an already-unpacked payload). A
machine-wide per-version lock makes concurrent first runs share one
download: one process fetches and verifies while every other process
waits and then reuses the committed store. Every later thin version
reuses those copies, so a stream of `@latest` patch bumps never
re-materializes the payloads. Installed payload versions coexist
rather than deleting one another during normal commands, so an older
thin client cannot force a re-download.

`engines.json` in the thin wheel records the source, version, sha256, and
license of every bundled binary and dependency; `tackbox doctor` fetches each
store if absent and verifies it (`engines-store`, `opengrep-store`).

## Android

The Android wheel uses the system `node` from PATH; `tackbox doctor`
checks its version. Android binaries link against Android system libraries
or are static:

- The thin wheel bundles the pinned ast-grep in `tackbox/bin` instead of
  depending on `ast-grep-cli`, which is declared only off Android.
- `jscpd` and `ast-grep` are built from their pinned crates for
  `aarch64-linux-android` with the pinned Rust and Android NDK
  (`engines/manifest.json`), reproducibly, linking only Android system
  libraries.
- `opengrep` is statically linked.
- The Go engines are built for `GOOS=android`: a Linux build is killed by
  Android's seccomp filter as soon as it starts `go` or `opengrep`.

The release cross-builds Android wheels on x86_64 Linux and smoke-tests them
in a pinned `termux-docker` image on an arm64 runner before publishing.
The container enables the standard `termux-exec` preload for uvx's shell
launchers. `verify-release` runs the published `uvx` canary in the same image.

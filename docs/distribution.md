# Distribution

How the `tackbox` wheel gets its engines. The store location, its overrides,
and the platforms are in the [README](../README.md#requirements).

`uvx tackbox@latest` installs one small platform wheel, `tackbox`, with the CLI
and tackbox's own analyzers; it is released on every push. The bundled
runtimes and third-party analyzers ship as two separate PyPI projects:

- `tackbox-engines` - the Node runtime (none on Android), `jscpd`, and the
  vendored third-party `node_modules`;
- `tackbox-opengrep` - opengrep and its required notices.

Neither is a pip dependency of `tackbox`. Each `tackbox` wheel pins the exact
version of both and the sha256 of each unpacked tree. On the first run for a
payload version, tackbox downloads the pinned wheel, verifies the unpacked
tree against the pin, and unpacks it once; concurrent first runs share one
download. Every later `tackbox` version that pins the same payload reuses it,
and installed payload versions coexist, so switching tackbox versions never
forces a re-download.

`tackbox doctor` fetches each store if absent and verifies it
(`engines-store`, `opengrep-store`).

## Android

The Android wheel has no bundled Node: it uses the system `node` from PATH,
and `tackbox doctor` checks its version. How the wheels are built:
[CONTRIBUTING.md](../CONTRIBUTING.md#releases).

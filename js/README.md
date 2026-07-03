# tackbox (JS / TS / Svelte)

ESLint plugin + browser report helper. Implements the
`error-reporting-and-coverage` and `error-handling-frontend` specs.

## Install

```bash
npm install --save-dev tackbox eslint
```

`@typescript-eslint/parser` and `svelte-eslint-parser` ship as
direct dependencies, so `.ts`, `.tsx`, `.svelte`, and
`<script lang="ts">` blocks work out of the box.

The plugin exposes a `recommended` config. Add to your
`eslint.config.js`:

```js
import tackbox from 'tackbox'

export default [
  {
    plugins: { tackbox },
    rules: tackbox.configs.recommended.rules,
  },
]
```

Or use the bundled preset directly via the bin wrapper:

```bash
npx tackbox-eslint src/**/*.{ts,svelte}
```

## Rules

| Rule                               | Summary                              |
|------------------------------------|--------------------------------------|
| `tackbox/no-swallow-catch`         | catch must throw, report, or marker  |
| `tackbox/no-swallow-promise-catch` | .catch(h) must throw, report, marker |
| `tackbox/no-console-error`         | banned; use `reportError`            |
| `tackbox/valid-error-report`       | static msg + cause + tags + dedupKey |
| `tackbox/valid-dedup-key`          | static `area.suffix[:identifier]`    |
| `tackbox/no-secret-in-report`      | no secret-named args in reporter     |
| `tackbox/no-throw-and-report`      | catch may not both throw and report  |
| `tackbox/ts-rethrow-without-cause` | `throw new` in catch needs `{cause}` |
| `tackbox/ts-useless-catch`         | catch that only re-throws is a no-op |
| `tackbox/ts-exit-in-catch`         | no `process.exit` inside catch       |

Full constraints per rule:

- `no-swallow-catch` - `catch` must throw, call a reporter, or have
  `// no-sentry: <reason>` above the `try`.
- `no-swallow-promise-catch` - `.catch(handler)` must throw, call a
  reporter, or carry the marker.
- `no-console-error` - `console.error` is banned; use `reportError`.
- `valid-error-report` - static 15-200 char msg, cause non-null,
  tags non-empty, dedupKey required.
- `valid-dedup-key` - dedupKey must be a static literal in
  `area.suffix[:identifier]` form.
- `no-secret-in-report` - reporter args must not reference
  `token` / `password` / `key` / `secret` / `cookie`.
- `no-throw-and-report` - `catch` may not both throw and report.
- `ts-rethrow-without-cause` - `throw new X(...)` in a `catch` must
  pass `{ cause: <caught> }` to preserve the stack chain.
- `ts-useless-catch` - a `catch` whose only statement re-throws the
  caught error is a no-op wrapper; remove the try/catch.
- `ts-exit-in-catch` - `process.exit(...)` inside a `catch` masks the
  exception; let it propagate.

## Reporter recognition

A call counts as a reporter only when its callee resolves to one of the
reporter names imported from `tackbox` / `tackbox/report` (tier-1), or
to a function declared in a repo-root `.tackbox-reporters` file
(tier-2). A bare identifier that merely shares the name is not trusted.

Names: `reportError`, `reportWarn`, `reportApiError`, `reportLayerError`
(4-arg form: msg, cause, tags, dedupKey) and `reportSynth`,
`reportSynthError` (3-arg form: msg, tags, dedupKey).

Tier-1 covers named, renamed, default- or namespace-member, and CJS
`require('tackbox/report')` forms. The strict argument contracts
(`valid-error-report`, `valid-dedup-key`, `no-secret-in-report`) apply
to tier-1 calls; declared sinks carry only the argument-flow contract
(the caught error must flow into the call).

`.tackbox-reporters` lines are `file#function: reason`. The `tackbox`
CLI parses and validates the file. When you consume this ESLint plugin
directly (without the CLI), populate `settings.tackbox.reporters` (a
list of `"file#function"` strings) in your own config; symbol
validation is the CLI's responsibility and is not performed in that
mode.

## Report helper

```js
import { init, reportError, reportWarn, setupGlobalHandlers, flush } from 'tackbox/report'

init({
  dsn: import.meta.env.VITE_SENTRY_DSN || '',
  release: import.meta.env.VITE_VERSION,
  verify: true,         // confirm connectivity at startup
  debug: false,
})
setupGlobalHandlers()
// ... on shutdown:
await flush(2000)

// in app code:
try {
  await fetchSomething()
} catch (err) {
  reportError('failed to fetch projects from API', err, { area: 'projects' }, 'projects.fetch')
}
```

Empty DSN: `init` logs a WARN (suppressible via `silentMissing`)
and stays log-only. `init({ verify: true })` sends one healthcheck
event with `fingerprint: ["report.startup"]` and flushes; glitchtip
groups all startups under one issue, no spam.

## Bundled API

- `init(opts)`, `flush(timeout)`, `verify(timeout)`, `isReady()`
- `reportError(msg, cause, tags, dedupKey)`
- `reportWarn(msg, cause, tags, dedupKey)`
- `reportSynthError(msg, tags, dedupKey)`
- `reportPanic(name, recovered)`
- `setupGlobalHandlers()` wires `window.error` and
  `window.unhandledrejection` to `reportError`

The `tackbox:error` custom event is dispatched on the window after
each `reportError`/`reportWarn`/`reportSynthError` call so a single
top-level component can render a toast.

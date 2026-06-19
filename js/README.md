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
| `tackbox/valid-throw-error`        | `throw new Error(msg)` msg is static |
| `tackbox/no-throw-and-report`      | catch may not both throw and report  |

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
- `valid-throw-error` - `throw new Error(msg)` msg must be a static
  15-200 char string.
- `no-throw-and-report` - `catch` may not both throw and report.

Reporter names matched on the final identifier of the callee:
`reportError`, `reportWarn`, `reportApiError`, `reportLayerError`
(4-arg form: msg, cause, tags, dedupKey) and `reportSynth`,
`reportSynthError` (3-arg form: msg, tags, dedupKey). Both bare
`reportError(...)` and `report.reportError(...)` are recognized.

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

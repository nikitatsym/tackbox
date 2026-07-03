// ESLint plugin: error-reporting-and-coverage rules for JS/TS/Svelte.
// Mirrors the Go ruleset (ERC001-006) with the frontend variants from
// the error-handling-frontend spec.

const noSwallowCatch = require('./rules/no-swallow-catch')
const noSwallowPromiseCatch = require('./rules/no-swallow-promise-catch')
const noConsoleError = require('./rules/no-console-error')
const validErrorReport = require('./rules/valid-error-report')
const noThrowAndReport = require('./rules/no-throw-and-report')
const validDedupKey = require('./rules/valid-dedup-key')
const noSecretInReport = require('./rules/no-secret-in-report')
const tsRethrowWithoutCause = require('./rules/ts-rethrow-without-cause')
const tsUselessCatch = require('./rules/ts-useless-catch')
const tsExitInCatch = require('./rules/ts-exit-in-catch')

const rules = {
  'no-swallow-catch': noSwallowCatch,
  'no-swallow-promise-catch': noSwallowPromiseCatch,
  'no-console-error': noConsoleError,
  'valid-error-report': validErrorReport,
  'no-throw-and-report': noThrowAndReport,
  'valid-dedup-key': validDedupKey,
  'no-secret-in-report': noSecretInReport,
  'ts-rethrow-without-cause': tsRethrowWithoutCause,
  'ts-useless-catch': tsUselessCatch,
  'ts-exit-in-catch': tsExitInCatch,
}

module.exports = {
  meta: { name: 'tackbox', version: '0.1.0' },
  rules,
  configs: {
    recommended: {
      plugins: ['tackbox'],
      rules: Object.fromEntries(
        Object.keys(rules).map(name => [`tackbox/${name}`, 'error']),
      ),
    },
  },
}

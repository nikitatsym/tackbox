const { test } = require('node:test')
const assert = require('node:assert')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { RuleTester } = require('eslint')

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: 'module' },
})

const noSwallow = require('../rules/no-swallow-catch')
const noSwallowPromise = require('../rules/no-swallow-promise-catch')
const noConsoleError = require('../rules/no-console-error')
const validReport = require('../rules/valid-error-report')

const decl = (code, invalid) => ({
  code,
  filename: 'app.js',
  settings: { tackbox: { reporters: ['app.js#myReport'] } },
  ...(invalid ? { errors: [{ messageId: 'swallow' }] } : {}),
})

// tier-1: name-trust is dead - a local function that merely shares a reporter
// name does not count as a reporter.
test('name-trust death: local reportError does not satisfy no-swallow-catch', () => {
  ruleTester.run('no-swallow-catch', noSwallow, {
    valid: [],
    invalid: [
      {
        code: "function reportError(m, e) {}\ntry { f() } catch (e) { reportError('handled it', e) }",
        errors: [{ messageId: 'swallow' }],
      },
    ],
  })
})

// tier-1: named / renamed / default-member / namespace-member / CJS destructure
// all resolve to a tackbox/report origin.
test('tier-1 import forms are recognized', () => {
  const body = "try { f() } catch (e) { %CALL% }"
  ruleTester.run('no-swallow-catch', noSwallow, {
    valid: [
      "import { reportError } from 'tackbox/report'\n" + body.replace('%CALL%', "reportError('connection lost mid-stream', e)"),
      "import { reportError as re } from 'tackbox/report'\n" + body.replace('%CALL%', "re('connection lost mid-stream', e)"),
      "import report from 'tackbox/report'\n" + body.replace('%CALL%', "report.reportError('connection lost mid-stream', e)"),
      "import * as report from 'tackbox/report'\n" + body.replace('%CALL%', "report.reportError('connection lost mid-stream', e)"),
      "const { reportError } = require('tackbox/report')\n" + body.replace('%CALL%', "reportError('connection lost mid-stream', e)"),
    ],
    invalid: [],
  })
})

// tier-2: a declared local function counts only when the caught error flows in.
test('tier-2 declaration with argument-flow', () => {
  ruleTester.run('no-swallow-catch', noSwallow, {
    valid: [
      decl("function myReport(m, e) {}\ntry { f() } catch (e) { myReport('handled', e) }"),
    ],
    invalid: [
      decl("function myReport(m, e) {}\ntry { f() } catch (e) { myReport('handled') }", true),
    ],
  })
})

// tier-2: the promise-catch handler parameter is an argument-flow source too.
test('tier-2 promise-catch argument-flow', () => {
  ruleTester.run('no-swallow-promise-catch', noSwallowPromise, {
    valid: [
      decl("function myReport(m, e) {}\np.catch(e => myReport('handled', e))"),
    ],
    invalid: [
      decl("function myReport(m) {}\np.catch(() => myReport('handled'))", true),
    ],
  })
})

// no-console-error is silent inside a declared reporter's body, loud outside.
test('no-console-error exempts declared reporter bodies', () => {
  ruleTester.run('no-console-error', noConsoleError, {
    valid: [
      {
        code: 'function myReport(m, e) { console.error(m, e) }',
        filename: 'app.js',
        settings: { tackbox: { reporters: ['app.js#myReport'] } },
      },
    ],
    invalid: [
      {
        code: "function other() { console.error('boom') }",
        filename: 'app.js',
        settings: { tackbox: { reporters: ['app.js#myReport'] } },
        errors: [{ messageId: 'use' }],
      },
    ],
  })
})

// tier-2 multi-extension: a `.svelte.ts` rune module declared as a reporter must
// match specifiers that omit the compound extension. A Svelte rune module keeps
// the double extension; `./errors`, `./errors.svelte`, and `./errors.svelte.ts`
// all name errors.svelte.ts and must all resolve to the declaration.
test('tier-2 multi-extension: shortened .svelte(.ts) specifiers match a .svelte.ts declaration', () => {
  const body = "\ntry { f() } catch (e) { myReport('connection dropped mid-stream', e) }"
  const forms = ['./errors.svelte.ts', './errors.svelte', './errors']
  ruleTester.run('no-swallow-catch', noSwallow, {
    valid: forms.map(spec => ({
      code: `import { myReport } from '${spec}'` + body,
      filename: 'app.js',
      settings: { tackbox: { reporters: ['errors.svelte.ts#myReport'] } },
    })),
    invalid: [],
  })
})

// tier-2 declared reporters carry only the argument-flow contract. The strict
// tier-1 signature checks (here valid-error-report's 4-arg dedupKey demand) must
// stay off them even after alias / multi-extension resolution recognizes them -
// a 3-arg reportError declared via a relative `.svelte` import earns no finding.
test('valid-error-report leaves tier-2 declared reporters alone (no dedupKey demand)', () => {
  ruleTester.run('valid-error-report', validReport, {
    valid: [
      {
        code: "import { reportError } from './errors.svelte'\ntry { f() } catch (e) { reportError('debug probe failed', e, { c: 'x' }) }",
        filename: 'app.js',
        settings: { tackbox: { reporters: ['errors.svelte.ts#reportError'] } },
      },
    ],
    invalid: [],
  })
})

// tier-2 $lib alias: a SvelteKit `$lib/...` import of a declared `.svelte.ts`
// reporter is recognized. Resolution is convention-based (nearest ancestor
// svelte.config.* -> its src/lib), so it holds on a fresh clone / CI where the
// generated .svelte-kit/tsconfig.json is absent. A real on-disk tree exercises
// the fs walk; an in-repo fixture tree is banned (self-lint would scan it).
async function lintTree(files, reporters) {
  const { ESLint } = require('eslint')
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tbx-lib-'))
  try {
    for (const [rel, code] of Object.entries(files)) {
      const abs = path.join(root, rel)
      fs.mkdirSync(path.dirname(abs), { recursive: true })
      fs.writeFileSync(abs, code)
    }
    const eslint = new ESLint({
      cwd: root,
      overrideConfigFile: path.join(__dirname, '..', '..', 'eslint.config.preset.js'),
      overrideConfig: [{ settings: { tackbox: { reporters } } }],
    })
    const targets = Object.keys(files)
      .filter(rel => /\.(svelte|ts|js)$/.test(rel) && !rel.endsWith('svelte.config.js'))
      .map(rel => path.join(root, rel))
    const results = await eslint.lintFiles(targets)
    return results.map(r => ({
      file: path.relative(root, r.filePath),
      errors: r.messages.filter(m => m.severity === 2).map(m => `${m.ruleId}:${m.line}`),
    }))
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
}

test('tier-2 $lib alias: aliased .svelte.ts reporter is recognized; bare/undeclared swallows still flagged', async () => {
  const reporterSrc =
    'export function reportError(msg, cause, tags) { void msg; void cause; void tags }\n'
  const consumer =
    "<script>\n" +
    "  import { reportError } from '$lib/stores/errors.svelte';\n" +
    "  function go() {\n" +
    "    doThing().catch((e) => reportError('inline edit commit failed', e, { c: 'x' }));\n" +
    "    try { risky() } catch (e) { reportError('inline edit focus failed', e, { c: 'y' }) }\n" +
    "  }\n" +
    "</script>\n"
  // A dropped error and a call to an undeclared local reporter must still be
  // caught - recognition widens for the declared alias only, nothing else.
  const bad =
    "<script>\n" +
    "  function reportError(m, e) { void m; void e }\n" +
    "  function go() {\n" +
    "    try { risky() } catch (e) { const _ = 1; void _ }\n" +
    "    try { risky() } catch (e) { reportError('local shadow not declared', e) }\n" +
    "  }\n" +
    "</script>\n"

  const out = await lintTree(
    {
      'frontend/svelte.config.js': 'export default {}\n',
      'frontend/src/lib/stores/errors.svelte.ts': reporterSrc,
      'frontend/src/lib/components/InlineEdit.svelte': consumer,
      'frontend/src/lib/components/Bad.svelte': bad,
    },
    ['frontend/src/lib/stores/errors.svelte.ts#reportError'],
  )
  const byFile = Object.fromEntries(out.map(r => [r.file, r.errors]))
  assert.deepEqual(
    byFile['frontend/src/lib/components/InlineEdit.svelte'],
    [],
    'aliased declared reporter should be recognized',
  )
  const badErrs = byFile['frontend/src/lib/components/Bad.svelte'] || []
  assert.equal(badErrs.length, 2, 'bare swallow + undeclared reporter must both flag: ' + badErrs.join(', '))
})

// report.js self-lints clean when the repo-root .tackbox/reporters declares
// reportPanic as the fatal-lane console sink (matching the real self-lint): the
// declaration exempts that one console.error, the catches carry no-report
// markers, and the reportError calls sit in event callbacks, not catches. This
// is an acceptance assert, not a resolution branch.
test('report.js self-lint is clean with reportPanic declared', async () => {
  const { ESLint } = require('eslint')
  const root = path.join(__dirname, '..', '..')
  const eslint = new ESLint({
    cwd: root,
    overrideConfigFile: path.join(root, 'eslint.config.preset.js'),
    overrideConfig: [{ settings: { tackbox: { reporters: ['js/report.js#reportPanic'] } } }],
  })
  const results = await eslint.lintFiles([path.join(__dirname, '..', 'report.js')])
  const errs = results.reduce((a, r) => a + r.errorCount + r.fatalErrorCount, 0)
  const msgs = results.flatMap(r => r.messages.map(m => `${m.ruleId}:${m.line} ${m.message}`))
  assert.equal(errs, 0, 'report.js not clean with reportPanic declared:\n' + msgs.join('\n'))
})

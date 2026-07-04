const { test } = require('node:test')
const assert = require('node:assert')
const path = require('node:path')
const { RuleTester } = require('eslint')

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: 'module' },
})

const noSwallow = require('../rules/no-swallow-catch')
const noSwallowPromise = require('../rules/no-swallow-promise-catch')
const noConsoleError = require('../rules/no-console-error')

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

// report.js self-lints clean under imports-only: its catches carry no-report
// markers and its reportError calls sit in event callbacks, not catches. This
// is an acceptance assert, not a resolution branch.
test('report.js self-lint is clean under imports-only', async () => {
  const { ESLint } = require('eslint')
  const eslint = new ESLint({
    overrideConfigFile: path.join(__dirname, '..', '..', 'eslint.config.preset.js'),
  })
  const results = await eslint.lintFiles([path.join(__dirname, '..', 'report.js')])
  const errs = results.reduce((a, r) => a + r.errorCount + r.fatalErrorCount, 0)
  const msgs = results.flatMap(r => r.messages.map(m => `${m.ruleId}:${m.line} ${m.message}`))
  assert.equal(errs, 0, 'report.js not clean under imports-only:\n' + msgs.join('\n'))
})

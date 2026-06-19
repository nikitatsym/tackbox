const { test } = require('node:test')
const { RuleTester } = require('eslint')

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: 'module' },
})

test('no-swallow-catch', () => {
  ruleTester.run('no-swallow-catch', require('../rules/no-swallow-catch'), {
    valid: [
      'try { f() } catch (e) { throw e }',
      'try { f() } catch (e) { reportError("connection lost mid-stream", e) }',
      '// no-sentry: bootstrap-only, no Sentry stack yet\ntry { f() } catch (e) {}',
    ],
    invalid: [
      { code: 'try { f() } catch (e) {}', errors: [{ messageId: 'swallow' }] },
      { code: 'try { f() } catch (e) { console.log(e) }', errors: [{ messageId: 'swallow' }] },
    ],
  })
})

test('no-swallow-promise-catch', () => {
  ruleTester.run('no-swallow-promise-catch', require('../rules/no-swallow-promise-catch'), {
    valid: [
      'p.catch(e => { throw e })',
      'p.catch(e => { reportError("api call failed mid-flight", e) })',
    ],
    invalid: [
      { code: 'p.catch(e => {})', errors: [{ messageId: 'swallow' }] },
      { code: 'p.catch(e => { console.log(e) })', errors: [{ messageId: 'swallow' }] },
      { code: 'p.catch(function (e) {})', errors: [{ messageId: 'swallow' }] },
    ],
  })
})

test('no-console-error', () => {
  ruleTester.run('no-console-error', require('../rules/no-console-error'), {
    valid: [
      'console.log("hi")',
      'reportError("connection lost mid-stream", err)',
    ],
    invalid: [
      { code: 'console.error("boom")', errors: [{ messageId: 'use' }] },
    ],
  })
})

test('valid-error-report', () => {
  ruleTester.run('valid-error-report', require('../rules/valid-error-report'), {
    valid: [
      'reportError("connection lost mid-stream", err, null, "api.lost")',
      'reportError("connection lost mid-stream", err, { area: "api" }, "api.lost")',
      'reportSynthError("retry budget exhausted at boot stage", null, "boot.retry")',
    ],
    invalid: [
      { code: 'reportError(`oops ${x}`, err, null, "api.lost")', errors: [{ messageId: 'msgNotStatic' }] },
      { code: 'reportError("short", err, null, "api.lost")', errors: [{ messageId: 'msgTooShort' }] },
      { code: 'reportError("connection lost mid-stream", null, null, "api.lost")', errors: [{ messageId: 'causeMissing' }] },
      { code: 'reportError("connection lost mid-stream", err, {}, "api.lost")', errors: [{ messageId: 'tagsEmpty' }] },
      { code: 'reportError("connection lost mid-stream", err)', errors: [{ messageId: 'dedupMissing' }] },
      { code: 'reportError()', errors: [{ messageId: 'noArgs' }] },
    ],
  })
})

test('valid-dedup-key', () => {
  ruleTester.run('valid-dedup-key', require('../rules/valid-dedup-key'), {
    valid: [
      'reportError("connection lost mid-stream", err, null, "api.lost")',
      'reportError("connection lost mid-stream", err, null, "api.lost:user_42")',
      'reportSynthError("retry budget exhausted at boot stage", null, "boot.retry")',
    ],
    invalid: [
      { code: 'reportError("connection lost mid-stream", err, null, key)', errors: [{ messageId: 'notLiteral' }] },
      { code: 'reportError("connection lost mid-stream", err, null, "BadFormat")', errors: [{ messageId: 'badFormat' }] },
      { code: 'reportError("connection lost mid-stream", err, null, "no_dot")', errors: [{ messageId: 'badFormat' }] },
    ],
  })
})

test('no-secret-in-report', () => {
  ruleTester.run('no-secret-in-report', require('../rules/no-secret-in-report'), {
    valid: [
      'reportError("connection lost mid-stream", err, { area: "api" }, "api.lost")',
      'reportSynth("retry budget exhausted at boot stage", { area: "api" }, "boot.retry")',
    ],
    invalid: [
      {
        code: 'reportError("connection lost mid-stream", err, { area: "api" }, token)',
        errors: [{ messageId: 'secretIdent' }],
      },
      {
        code: 'reportError("connection lost mid-stream", err, { sessionToken: x }, "api.lost")',
        errors: [{ messageId: 'secretIdent' }],
      },
      {
        code: 'reportSynth("password token leaked from cookie", null, "auth.password")',
        errors: [
          { messageId: 'secretString' },
          { messageId: 'secretString' },
        ],
      },
      {
        code: 'reportLayerError("processing request failed at gateway", err, { auth: "bearer token here" }, "api.auth")',
        errors: [{ messageId: 'secretString' }],
      },
    ],
  })
})

test('valid-throw-error', () => {
  ruleTester.run('valid-throw-error', require('../rules/valid-throw-error'), {
    valid: [
      'throw new Error("connection lost mid-stream")',
      'throw e',
    ],
    invalid: [
      { code: 'throw new Error(`oops ${x}`)', errors: [{ messageId: 'notStatic' }] },
      { code: 'throw new Error("short")', errors: [{ messageId: 'tooShort' }] },
    ],
  })
})

test('no-throw-and-report', () => {
  ruleTester.run('no-throw-and-report', require('../rules/no-throw-and-report'), {
    valid: [
      'try { f() } catch (e) { throw e }',
      'try { f() } catch (e) { reportError("api call failed mid-flight", e, null, "api.fail") }',
    ],
    invalid: [
      {
        code: 'try { f() } catch (e) { reportError("api call failed mid-flight", e, null, "api.fail"); throw e }',
        errors: [{ messageId: 'both' }],
      },
    ],
  })
})

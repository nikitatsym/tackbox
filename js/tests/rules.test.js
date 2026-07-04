const { test } = require('node:test')
const { RuleTester } = require('eslint')

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: 'module' },
})

// The Result-boundary exit (F2) keys off the enclosing function's return-type
// annotation, so those fixtures need the TS parser (espree has no returnType).
const tsRuleTester = new RuleTester({
  languageOptions: { parser: require('@typescript-eslint/parser'), ecmaVersion: 2022, sourceType: 'module' },
})

// Reporter calls are recognized only through a tackbox/report import (tier-1)
// or a .tackbox-reporters declaration (tier-2); a bare name is not trusted.
// These rule tests import the canonical reporters so the calls resolve.
const R =
  "import { reportError, reportSynth, reportSynthError, reportApiError, reportWarn, reportLayerError } from 'tackbox/report'\n"

test('no-swallow-catch', () => {
  ruleTester.run('no-swallow-catch', require('../rules/no-swallow-catch'), {
    valid: [
      'try { f() } catch (e) { throw e }',
      R + 'try { f() } catch (e) { reportError("connection lost mid-stream", e) }',
      // reporter anywhere in the block (after other statements) keeps the catch
      // clean: block-scan, pinned so a future path-sensitive port cannot regress it (F2).
      R + 'try { f() } catch (e) { cleanupState(); logLocally(e); reportError("connection lost mid-stream", e) }',
      '// no-report: bootstrap-only, no Sentry stack yet\ntry { f() } catch (e) {}',
      '// no-report: bootstrap-only, no Sentry stack yet, a reason long\n// enough that splitting it across lines is the point\ntry { f() } catch (e) {}',
    ],
    invalid: [
      { code: 'try { f() } catch (e) {}', errors: [{ messageId: 'swallow' }] },
      { code: 'try { f() } catch (e) { console.log(e) }', errors: [{ messageId: 'swallow' }] },
      { code: '// no-report: reason\n\ntry { f() } catch (e) {}', errors: [{ messageId: 'swallow' }] },
    ],
  })
})

// F2: typed Result-boundary is the third legal catch exit (throw / reporter /
// boundary). Legal only when the enclosing fn is annotated Result / Attempt /
// Promise<Result|Attempt> AND the caught err flows into `{ ok:false, cause:err }`.
test('no-swallow-catch result-boundary (F2)', () => {
  tsRuleTester.run('no-swallow-catch', require('../rules/no-swallow-catch'), {
    valid: [
      'function f(): Result<T> { try { g() } catch (e) { return { ok: false, cause: e } } }',
      'function f(): Attempt { try { g() } catch (e) { return { ok: false, message: e } } }',
      'async function f(): Promise<Result<T>> { try { await g() } catch (e) { return { ok: false, cause: e } } }',
    ],
    invalid: [
      // bare { ok:false } drops the caught error -> swallow.
      { code: 'function f(): Result<T> { try { g() } catch (e) { return { ok: false } } }', errors: [{ messageId: 'swallow' }] },
      // boundary carries some other identifier, not the caught err -> swallow.
      { code: 'function f(): Result<T> { try { g() } catch (e) { return { ok: false, cause: other } } }', errors: [{ messageId: 'swallow' }] },
      // no Result annotation on the enclosing fn -> no boundary credit (annotation-based).
      { code: 'function f() { try { g() } catch (e) { return { ok: false, cause: e } } }', errors: [{ messageId: 'swallow' }] },
      // non-Result return annotation -> no credit.
      { code: 'function f(): void { try { g() } catch (e) { return { ok: false, cause: e } } }', errors: [{ messageId: 'swallow' }] },
    ],
  })
})

// F2b: one coherent path-sensitive analysis over all three exits. Every path
// must terminate (throw / boundary) or pass a sticky reporter before the end;
// a path reaching the end without an event is a finding. Reporters need not be
// terminal (sticky). Opaque constructs (switch/loop) do not surface events.
test('no-swallow-catch path-sensitive (F2b)', () => {
  ruleTester.run('no-swallow-catch', require('../rules/no-swallow-catch'), {
    valid: [
      // both branches handled (reporter on one, throw on the other).
      R + 'try { f() } catch (e) { if (x) { reportError("connection lost mid-stream", e) } else { throw e } }',
      // reporter is sticky: statements after it on the same path are fine.
      R + 'try { f() } catch (e) { reportError("connection lost mid-stream", e); cleanup() }',
    ],
    invalid: [
      // throw on only one branch: the else path falls through (flip of block-scan).
      { code: 'try { f() } catch (e) { if (x) { throw e } }', errors: [{ messageId: 'swallow' }] },
      // reporter on only one branch: the else path swallows (flip of block-scan).
      { code: R + 'try { f() } catch (e) { if (x) { reportError("connection lost mid-stream", e) } }', errors: [{ messageId: 'swallow' }] },
      // switch is opaque: a reporter inside it does not count as a path event.
      { code: R + 'try { f() } catch (e) { switch (x) { case 1: reportError("connection lost mid-stream", e) } }', errors: [{ messageId: 'swallow' }] },
    ],
  })
})

test('no-swallow-catch path-sensitive boundary (F2b)', () => {
  tsRuleTester.run('no-swallow-catch', require('../rules/no-swallow-catch'), {
    valid: [
      // both branches terminate: boundary on one, throw on the other.
      'function f(): Result<T> { try { g() } catch (e) { if (x) { return { ok: false, cause: e } } else { throw e } } }',
    ],
    invalid: [
      // boundary on one branch, the else path just logs and falls through.
      { code: 'function f(): Result<T> { try { g() } catch (e) { if (x) { return { ok: false, cause: e } } else { log(e) } } }', errors: [{ messageId: 'swallow' }] },
    ],
  })
})

test('no-swallow-promise-catch', () => {
  ruleTester.run('no-swallow-promise-catch', require('../rules/no-swallow-promise-catch'), {
    valid: [
      'p.catch(e => { throw e })',
      R + 'p.catch(e => { reportError("api call failed mid-flight", e) })',
    ],
    invalid: [
      { code: 'p.catch(e => {})', errors: [{ messageId: 'swallow' }] },
      { code: 'p.catch(e => { console.log(e) })', errors: [{ messageId: 'swallow' }] },
      { code: 'p.catch(function (e) {})', errors: [{ messageId: 'swallow' }] },
    ],
  })
})

// Result-boundary conversion is NOT accepted in a promise .catch handler (gmux
// allowBoundary:false): the enclosing fn's Result type does not govern the
// callback's return, so it stays a swallow even under Promise<Result<T>>.
test('no-swallow-promise-catch result-boundary refusal (F2)', () => {
  tsRuleTester.run('no-swallow-promise-catch', require('../rules/no-swallow-promise-catch'), {
    valid: [],
    invalid: [
      { code: 'function f(): Promise<Result<T>> { return p.catch(e => { return { ok: false, cause: e } }) }', errors: [{ messageId: 'swallow' }] },
    ],
  })
})

// F2b: the same path-sensitive analysis governs promise .catch handlers.
test('no-swallow-promise-catch path-sensitive (F2b)', () => {
  ruleTester.run('no-swallow-promise-catch', require('../rules/no-swallow-promise-catch'), {
    valid: [
      R + 'p.catch(e => { if (x) { reportError("api call failed mid-flight", e) } else { throw e } })',
    ],
    invalid: [
      // reporter on only one branch: the else path swallows (flip of block-scan).
      { code: R + 'p.catch(e => { if (x) { reportError("api call failed mid-flight", e) } })', errors: [{ messageId: 'swallow' }] },
    ],
  })
})

test('no-console-error', () => {
  ruleTester.run('no-console-error', require('../rules/no-console-error'), {
    valid: [
      'console.log("hi")',
      'foo("connection lost mid-stream", err)',
    ],
    invalid: [
      { code: 'console.error("boom")', errors: [{ messageId: 'use' }] },
    ],
  })
})

test('valid-error-report', () => {
  ruleTester.run('valid-error-report', require('../rules/valid-error-report'), {
    valid: [
      R + 'reportError("connection lost mid-stream", err, null, "api.lost")',
      R + 'reportError("connection lost mid-stream", err, { area: "api" }, "api.lost")',
      R + 'reportSynthError("retry budget exhausted at boot stage", null, "boot.retry")',
    ],
    invalid: [
      { code: R + 'reportError(`oops ${x}`, err, null, "api.lost")', errors: [{ messageId: 'msgNotStatic' }] },
      { code: R + 'reportError("short", err, null, "api.lost")', errors: [{ messageId: 'msgTooShort' }] },
      { code: R + 'reportError("connection lost mid-stream", null, null, "api.lost")', errors: [{ messageId: 'causeMissing' }] },
      { code: R + 'reportError("connection lost mid-stream", err, {}, "api.lost")', errors: [{ messageId: 'tagsEmpty' }] },
      { code: R + 'reportError("connection lost mid-stream", err)', errors: [{ messageId: 'dedupMissing' }] },
      { code: R + 'reportError()', errors: [{ messageId: 'noArgs' }] },
    ],
  })
})

test('valid-dedup-key', () => {
  ruleTester.run('valid-dedup-key', require('../rules/valid-dedup-key'), {
    valid: [
      R + 'reportError("connection lost mid-stream", err, null, "api.lost")',
      R + 'reportError("connection lost mid-stream", err, null, "api.lost:user_42")',
      R + 'reportSynthError("retry budget exhausted at boot stage", null, "boot.retry")',
    ],
    invalid: [
      { code: R + 'reportError("connection lost mid-stream", err, null, key)', errors: [{ messageId: 'notLiteral' }] },
      { code: R + 'reportError("connection lost mid-stream", err, null, "BadFormat")', errors: [{ messageId: 'badFormat' }] },
      { code: R + 'reportError("connection lost mid-stream", err, null, "no_dot")', errors: [{ messageId: 'badFormat' }] },
    ],
  })
})

test('no-secret-in-report', () => {
  ruleTester.run('no-secret-in-report', require('../rules/no-secret-in-report'), {
    valid: [
      R + 'reportError("connection lost mid-stream", err, { area: "api" }, "api.lost")',
      R + 'reportSynth("retry budget exhausted at boot stage", { area: "api" }, "boot.retry")',
    ],
    invalid: [
      {
        code: R + 'reportError("connection lost mid-stream", err, { area: "api" }, token)',
        errors: [{ messageId: 'secretIdent' }],
      },
      {
        code: R + 'reportError("connection lost mid-stream", err, { sessionToken: x }, "api.lost")',
        errors: [{ messageId: 'secretIdent' }],
      },
      {
        code: R + 'reportSynth("password token leaked from cookie", null, "auth.password")',
        errors: [
          { messageId: 'secretString' },
          { messageId: 'secretString' },
        ],
      },
      {
        code: R + 'reportLayerError("processing request failed at gateway", err, { auth: "bearer token here" }, "api.auth")',
        errors: [{ messageId: 'secretString' }],
      },
    ],
  })
})

test('no-throw-and-report', () => {
  ruleTester.run('no-throw-and-report', require('../rules/no-throw-and-report'), {
    valid: [
      'try { f() } catch (e) { throw e }',
      R + 'try { f() } catch (e) { reportError("api call failed mid-flight", e, null, "api.fail") }',
    ],
    invalid: [
      {
        code: R + 'try { f() } catch (e) { reportError("api call failed mid-flight", e, null, "api.fail"); throw e }',
        errors: [{ messageId: 'both' }],
      },
    ],
  })
})

test('ts-rethrow-without-cause', () => {
  ruleTester.run('ts-rethrow-without-cause', require('../rules/ts-rethrow-without-cause'), {
    valid: [
      'try { f() } catch (e) { throw new Error("wrap failed", { cause: e }) }',
      'try { f() } catch (e) { throw new WrapError("bad gateway", { status: 502, cause: e }) }',
      'try { f() } catch (e) { throw e }',
      'try { f() } catch { throw new Error("no binding to chain") }',
      'throw new Error("not inside a catch")',
      'try { f() } catch (e) { throw new AggregateError([e], "all downstream calls failed") }',
      'try { f() } catch (e) { throw new AggregateError([first, e], "batch had failures") }',
    ],
    invalid: [
      { code: 'try { f() } catch (e) { throw new Error("connection dropped") }', errors: [{ messageId: 'noCause' }] },
      { code: 'try { f() } catch (e) { throw new HttpError("bad gateway", { status: 502 }) }', errors: [{ messageId: 'noCause' }] },
      { code: 'try { f() } catch (e) { throw new Error("wrong cause", { cause: other }) }', errors: [{ messageId: 'noCause' }] },
      { code: 'try { f() } catch (e) { throw new AggregateError([], "nothing captured") }', errors: [{ messageId: 'noCause' }] },
      { code: 'try { f() } catch (e) { throw new AggregateError([other], "wrong error kept") }', errors: [{ messageId: 'noCause' }] },
    ],
  })
})

test('ts-useless-catch', () => {
  ruleTester.run('ts-useless-catch', require('../rules/ts-useless-catch'), {
    valid: [
      'try { f() } catch (e) { throw new Error("wrap failed", { cause: e }) }',
      'try { f() } catch (e) { log(e); throw e }',
      'try { f() } catch (e) { throw other }',
      'try { f() } catch {}',
    ],
    invalid: [
      { code: 'try { f() } catch (e) { throw e }', errors: [{ messageId: 'useless' }] },
    ],
  })
})

test('ts-exit-in-catch', () => {
  ruleTester.run('ts-exit-in-catch', require('../rules/ts-exit-in-catch'), {
    valid: [
      'try { f() } catch (e) { throw e }',
      'try { f() } catch (e) { cleanup() }',
      'process.exit(1)',
    ],
    invalid: [
      { code: 'try { f() } catch (e) { process.exit(1) }', errors: [{ messageId: 'exit' }] },
      { code: 'try { f() } catch { process.exit(2) }', errors: [{ messageId: 'exit' }] },
    ],
  })
})

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

// F7a: the rejection handler of a two-arg .then(onOk, onErr) is a full
// rejection handler - run through the SAME path-sensitive analysis as .catch
// (allowBoundary:false). .then(ok) alone propagates the rejection (not a
// finding); .then(null, onErr) is .catch-equivalent; a non-function second arg
// (e.g. null) is .then(ok)-equivalent.
test('no-swallow-promise-catch two-arg then (F7a)', () => {
  ruleTester.run('no-swallow-promise-catch', require('../rules/no-swallow-promise-catch'), {
    valid: [
      // single-arg then: the rejection propagates naturally, no handler to check.
      'p.then(v => use(v))',
      // onErr rethrows on every path.
      'p.then(v => use(v), e => { throw e })',
      // onErr reports (reporter resolved through the tackbox import).
      R + 'p.then(v => use(v), e => { reportError("api call failed mid-flight", e) })',
      // .then(null, onErr) is .catch-equivalent; a handled onErr is clean.
      'p.then(null, e => { throw e })',
      // second arg is not a function literal (null) -> equivalent to .then(ok).
      'p.then(v => use(v), null)',
    ],
    invalid: [
      // onErr ignores the rejection entirely: swallow.
      { code: 'p.then(v => use(v), e => {})', errors: [{ messageId: 'swallow' }] },
      { code: 'p.then(v => use(v), e => { cleanup() })', errors: [{ messageId: 'swallow' }] },
      // .then(null, onErr) that only logs: swallow.
      { code: 'p.then(null, e => { console.log(e) })', errors: [{ messageId: 'swallow' }] },
      // fail closed: a reportError NOT imported from tackbox does not resolve to
      // a reporter (name-only match is dead), so this onErr swallows.
      { code: 'p.then(v => use(v), e => { reportError("api call failed mid-flight", e) })', errors: [{ messageId: 'swallow' }] },
    ],
  })
})

// F7cal: two consumer-calibration exits. (1) calling the enclosing `new
// Promise(...)` executor's reject parameter is the promise's own rethrow -
// resolution is structural (the scope binding must be that parameter), a
// free-standing function named `reject` earns nothing. (2) an identity onErr
// (`e => e`) settles the chain with the caught error object itself - the
// recognized rejection-to-value idiom; any wrapper object stays a swallow
// (the F2 boundary refusal is untouched).
test('no-swallow-promise-catch executor-reject and identity (F7cal)', () => {
  ruleTester.run('no-swallow-promise-catch', require('../rules/no-swallow-promise-catch'), {
    valid: [
      // reject(e): the executor's second parameter, err object flows in.
      'new Promise((resolve, reject) => { doThing().then(v => resolve(v), e => reject(e)) })',
      // reject with a wrapped error still carries the object (cause).
      'new Promise((resolve, reject) => { doThing().then(v => resolve(v), e => { cleanup(); reject(new Error("op failed", { cause: e })) }) })',
      // identity onErr: the settled value IS the caught error object.
      'const failure = op.then(() => null, err => err)',
      'p.catch(e => e)',
      // identity on one path, rethrow on the other: both terminate.
      'p.then(v => use(v), e => { if (transient(e)) return e; throw e })',
    ],
    invalid: [
      // a free-standing function named reject is not the executor parameter.
      { code: 'function reject(e) { count += 1 }\np.then(v => use(v), e => reject(e))', errors: [{ messageId: 'swallow' }] },
      // reject fed the stringified error: the object dies on the way out.
      { code: 'new Promise((resolve, reject) => { p.then(v => resolve(v), e => reject(e.message)) })', errors: [{ messageId: 'swallow' }] },
      // stringified identity is not identity.
      { code: 'const failure = op.then(() => null, err => err.message)', errors: [{ messageId: 'swallow' }] },
      // a fall-through path drops the rejection.
      { code: 'op.then(() => null, err => { if (x) return err; })', errors: [{ messageId: 'swallow' }] },
      // a plain-object carrier is not the error itself (F2 refusal holds).
      { code: 'op.then(() => null, err => ({ wrapped: err }))', errors: [{ messageId: 'swallow' }] },
    ],
  })
})

// F7cal: reject(e) is equally the terminal exit of a sync catch inside the
// executor.
test('no-swallow-catch executor-reject (F7cal)', () => {
  ruleTester.run('no-swallow-catch', require('../rules/no-swallow-catch'), {
    valid: [
      'new Promise((resolve, reject) => { try { resolve(f()) } catch (e) { reject(e) } })',
    ],
    invalid: [
      { code: 'function reject(e) { count += 1 }\nnew Promise((resolve, rej) => { try { resolve(f()) } catch (e) { reject(e) } })', errors: [{ messageId: 'swallow' }] },
    ],
  })
})

// F7b: a bound/used Promise.allSettled result launders rejections into values;
// it is a finding unless the enclosing function contains at least one syntactic
// `.reason` access (fail closed, per F2b - allSettled is rare enough that the
// coarse gate is acceptable). Passing the result whole to a helper is opaque
// (no visible `.reason`) and is a finding. Escape: `// no-report: <reason>`.
test('no-swallow-allsettled (F7b)', () => {
  ruleTester.run('no-swallow-allsettled', require('../rules/no-swallow-allsettled'), {
    valid: [
      // rejected reasons are inspected in the same scope.
      "const rs = await Promise.allSettled(ps); for (const r of rs) { if (r.status === 'rejected') report(r.reason) }",
      // `.reason` reached inside a nested callback (the scan descends into it).
      "async function f() { const rs = await Promise.allSettled(ps); rs.filter(r => r.status === 'rejected').forEach(r => log(r.reason)) }",
      // computed `.reason` access also counts.
      "const rs = await Promise.allSettled(ps); rs.forEach(r => handle(r['reason']))",
      // marker escape (would be a finding without it - see the invalid twin).
      '// no-report: partial batch, failures surfaced by the caller\nconst rs = await Promise.allSettled(ps); use(rs)',
      // marker-escaped fire-and-forget.
      '// no-report: best-effort broadcast, outcomes intentionally dropped\nawait Promise.allSettled(ps)',
    ],
    invalid: [
      // fire-and-forget discards every outcome: allSettled never rejects, so
      // this is the quietest swallow of all.
      { code: 'await Promise.allSettled(ps)', errors: [{ messageId: 'swallow' }] },
      // only fulfilled values are read; rejected reasons are dropped.
      { code: "const rs = await Promise.allSettled(ps); const ok = rs.filter(r => r.status === 'fulfilled').map(r => r.value)", errors: [{ messageId: 'swallow' }] },
      // the result is handed whole to a helper: opaque, no visible `.reason`.
      { code: 'async function f() { const rs = await Promise.allSettled(ps); return processAll(rs) }', errors: [{ messageId: 'swallow' }] },
      // bound through a .then continuation with no `.reason` touch.
      { code: 'Promise.allSettled(ps).then(rs => { doStuff(rs) })', errors: [{ messageId: 'swallow' }] },
      // only the count is used.
      { code: 'const rs = await Promise.allSettled(ps); log(rs.length)', errors: [{ messageId: 'swallow' }] },
    ],
  })
})

// F7c: a try containing JSON.parse must propagate the parse error - every catch
// path throws the caught object or converts to a Result boundary carrying it
// (object-flow principle F5; stringification breaks the chain). A fallback
// value, a report-and-continue, or a stringified rethrow swallows it
// (report+default = finding). Escape: `// parse-skip: <reason>` above the try.
test('no-parse-fallback (F7c)', () => {
  ruleTester.run('no-parse-fallback', require('../rules/no-parse-fallback'), {
    valid: [
      // bare rethrow of the caught error object.
      'try { const x = JSON.parse(s) } catch (e) { throw e }',
      // rewrap preserving the object via `cause`.
      "try { JSON.parse(s) } catch (e) { throw new Error('bad config payload', { cause: e }) }",
      // report then rethrow: reported AND propagated.
      R + 'try { JSON.parse(s) } catch (e) { reportError("config parse failed mid-load", e); throw e }',
      // two-step wrap: the object flows through a local carrier (F5 credits it).
      "try { JSON.parse(s) } catch (e) { const wrapped = new Error('bad config', { cause: e }); throw wrapped }",
      // both branches throw the object.
      "try { JSON.parse(s) } catch (e) { if (x) { throw e } else { throw new Error('parse failed', { cause: e }) } }",
      // marker escape (the twin below without the marker is a finding).
      '// parse-skip: optional config, absence is expected\ntry { JSON.parse(s) } catch (e) { useDefault() }',
      // no catch: the parse error propagates through finally, nothing swallows it.
      'try { JSON.parse(s) } finally { cleanup() }',
      // no surrounding try: the error propagates naturally, out of scope.
      'const x = JSON.parse(s)',
    ],
    invalid: [
      // fallback value instead of propagating.
      { code: 'function f() { try { JSON.parse(s) } catch (e) { return {} } }', errors: [{ messageId: 'fallback' }] },
      // stringified rethrow drops the error object (chain break).
      { code: "try { JSON.parse(s) } catch (e) { throw new Error(e.message) }", errors: [{ messageId: 'fallback' }] },
      // throw a fresh error that does not carry the caught one.
      { code: "try { JSON.parse(s) } catch (e) { throw new Error('parse failed') }", errors: [{ messageId: 'fallback' }] },
      // report + default: reporting does not license the fallback.
      { code: R + 'function f() { try { JSON.parse(s) } catch (e) { reportError("config parse failed mid-load", e); return {} } }', errors: [{ messageId: 'fallback' }] },
      // report only, then fall through the end of the catch.
      { code: R + 'try { JSON.parse(s) } catch (e) { reportError("config parse failed mid-load", e) }', errors: [{ messageId: 'fallback' }] },
      // throw on one branch only; the other path falls through.
      { code: 'try { JSON.parse(s) } catch (e) { if (x) { throw e } }', errors: [{ messageId: 'fallback' }] },
    ],
  })
})

// F7c boundary: a Result boundary is a legal parse-error exit only when the
// enclosing fn is annotated Result/Attempt AND the caught error flows in as an
// object (a stringified `message: e.message` breaks the chain, like F2).
test('no-parse-fallback result-boundary (F7c)', () => {
  tsRuleTester.run('no-parse-fallback', require('../rules/no-parse-fallback'), {
    valid: [
      'function f(): Result<T> { try { JSON.parse(s) } catch (e) { return { ok: false, cause: e } } }',
      'async function f(): Promise<Result<T>> { try { JSON.parse(await read()) } catch (e) { return { ok: false, cause: e } } }',
    ],
    invalid: [
      // stringified boundary: message carries only the text, not the object.
      { code: 'function f(): Result<T> { try { JSON.parse(s) } catch (e) { return { ok: false, message: e.message } } }', errors: [{ messageId: 'fallback' }] },
      // no Result annotation on the enclosing fn: no boundary credit.
      { code: 'function f() { try { JSON.parse(s) } catch (e) { return { ok: false, cause: e } } }', errors: [{ messageId: 'fallback' }] },
      // bare { ok: false } drops the caught error.
      { code: 'function f(): Result<T> { try { JSON.parse(s) } catch (e) { return { ok: false } } }', errors: [{ messageId: 'fallback' }] },
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

// A skip/todo/skipIf/fixme in a chain rooted at bare it/test/describe, or a
// bare xit/xdescribe/xtest, drops the test unless it carries a non-empty
// in-call reason (node:test options skip/todo, playwright (cond, 'reason'))
// or a // test-skip: <reason> marker above the statement. Chained forms
// (skipIf(...)(...), skip.each(...)(...)) report once, on the inner call
// carrying the skip property. A deeper root (queue.skip, foo.test.skip) is
// out of scope.
test('no-skipped-test', () => {
  ruleTester.run('no-skipped-test', require('../rules/no-skipped-test'), {
    valid: [
      'it("runs", () => {})',
      '// test-skip: flaky upstream, issue 12\nit.skip("later", () => {})',
      'queue.skip()',
      'foo.test.skip("x")',
      '// test-skip: pending backend, issue 34\nit.skipIf(cond)("n", f)',
      // node:test options with a reason; non-literal reasons are trusted.
      'it("n", { skip: "flaky upstream, issue 12" }, () => {})',
      'test("n", { todo: "needs api endpoint" }, () => {})',
      'test({ skip: "no name form, reason present" }, () => {})',
      'it("n", { skip: why }, () => {})',
      'it("n", { skip: `${why}` }, () => {})',
      // falsy skip is not a skip; unrelated options are not inspectable.
      'it("n", { skip: false }, () => {})',
      'it("n", { concurrency: 2 }, () => {})',
      '// test-skip: quarantined, issue 7\nit("n", { skip: true }, () => {})',
      // playwright conditional with reason; non-literal reason trusted.
      'test.skip(isMobile, "touch-only flow")',
      'test.fixme(isWebkit, "portal rendering, issue 9")',
      'test.skip(isMobile, reasonFor(env))',
    ],
    invalid: [
      { code: 'it.skip("later", () => {})', errors: [{ messageId: 'skipped' }] },
      { code: 'test.todo("write this")', errors: [{ messageId: 'skipped' }] },
      { code: 'xit("x", () => {})', errors: [{ messageId: 'skipped' }] },
      { code: 'xdescribe("grp", () => {})', errors: [{ messageId: 'skipped' }] },
      { code: 'it.skipIf(isCi)("n", f)', errors: [{ messageId: 'skipped' }] },
      { code: '// test-skip:\nit.skip("x", () => {})', errors: [{ messageId: 'skipped' }] },
      { code: '// test-skip: reason\n\nit.skip("x", () => {})', errors: [{ messageId: 'skipped' }] },
      { code: 'it.skip.each([1])("n", f)', errors: [{ messageId: 'skipped' }] },
      // node:test reasonless options.
      { code: 'it("n", { skip: true }, () => {})', errors: [{ messageId: 'skipped' }] },
      { code: 'test("n", { skip: "" }, () => {})', errors: [{ messageId: 'skipped' }] },
      { code: 'it("n", { skip: "   " }, () => {})', errors: [{ messageId: 'skipped' }] },
      { code: 'test("n", { todo: true }, () => {})', errors: [{ messageId: 'skipped' }] },
      // playwright cond-only, bare in-body, and declaration forms.
      { code: 'test.skip(isMobile)', errors: [{ messageId: 'skipped' }] },
      { code: 'test.skip()', errors: [{ messageId: 'skipped' }] },
      { code: 'test.skip("title", () => {})', errors: [{ messageId: 'skipped' }] },
      { code: 'test.fixme()', errors: [{ messageId: 'skipped' }] },
    ],
  })
})

// A .only in a chain rooted at bare it/test/describe, or a bare fit/fdescribe/
// ftest, focuses the suite and disables the rest. No escape hatch. Chained
// only.each(...)(...) reports once, on the inner call. A deeper root
// (myobj.only) is out of scope.
test('no-focused-test', () => {
  ruleTester.run('no-focused-test', require('../rules/no-focused-test'), {
    valid: [
      'it("x", f)',
      'myobj.only("x")',
    ],
    invalid: [
      { code: 'it.only("x", f)', errors: [{ messageId: 'focused' }] },
      { code: 'describe.only("g", f)', errors: [{ messageId: 'focused' }] },
      { code: 'fit("x", f)', errors: [{ messageId: 'focused' }] },
      { code: 'fdescribe("g", f)', errors: [{ messageId: 'focused' }] },
      { code: 'test.only.each([1])("n", f)', errors: [{ messageId: 'focused' }] },
    ],
  })
})

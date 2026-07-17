const { test } = require('node:test')
const assert = require('node:assert')

// report.js reads console, window/CustomEvent, and @sentry/browser at call time
// (globals + the shared module cache), so replacing them here reaches the module
// under test. A fake Sentry in require.cache dodges real init/network; load()
// reloads report.js per scenario for pristine ready/lastSent state.
const REPORT_PATH = require.resolve('../report.js')
const SENTRY_PATH = require.resolve('@sentry/browser')

console.log = () => {}
console.warn = () => {}
console.error = () => {}

let captured = [] // one entry per Sentry.captureException: { err, level, fingerprint, tags }
let dispatched = [] // one entry per window.dispatchEvent: the CustomEvent detail
let scope = null

const fakeSentry = {
  init() {},
  withScope(fn) {
    scope = { level: undefined, fingerprint: undefined, tags: {} }
    fn({
      setLevel(l) { scope.level = l },
      setFingerprint(f) { scope.fingerprint = f },
      setTag(k, v) { scope.tags[k] = v },
    })
    scope = null
  },
  captureException(err) {
    captured.push({
      err,
      level: scope && scope.level,
      fingerprint: scope && scope.fingerprint,
      tags: scope ? { ...scope.tags } : null,
    })
  },
}
require.cache[SENTRY_PATH] = { id: SENTRY_PATH, filename: SENTRY_PATH, loaded: true, exports: fakeSentry }

class FakeCustomEvent {
  constructor(type, opts) {
    this.type = type
    this.detail = opts && opts.detail
  }
}
Object.defineProperty(globalThis, 'CustomEvent', { value: FakeCustomEvent, configurable: true, writable: true })
Object.defineProperty(globalThis, 'window', {
  value: { addEventListener() {}, dispatchEvent(ev) { dispatched.push(ev.detail); return true } },
  configurable: true,
  writable: true,
})

const VALID_DSN = 'https://public@sentry.example.com/1'

function load() {
  captured = []
  dispatched = []
  scope = null
  delete require.cache[REPORT_PATH]
  return require(REPORT_PATH)
}

// module.exports pins the exact ordered reporting-only key list.
test('module.exports is the exact ordered reporting-only key list', () => {
  const report = load()
  assert.deepEqual(Object.keys(report), [
    'init', 'flush', 'isReady', 'verify', 'reportError', 'reportWarn',
    'reportQuiet', 'reportSynthError', 'notify', 'reportPanic',
  ])
})

// (a) DSN unset -> not ready -> capture disabled, but the user lane still fires.
test('report dispatches to the user lane even when not ready (DSN unset)', () => {
  const report = load()
  report.init({})
  assert.equal(report.isReady(), false)
  report.reportError('connection lost mid-stream', new Error('boom'), { area: 'net' }, 'net.conn')
  assert.equal(dispatched.length, 1, 'user lane must dispatch without init')
  assert.equal(captured.length, 0, 'capture stays gated off when not ready')
  assert.equal(dispatched[0].msg, 'connection lost mid-stream')
  assert.equal(dispatched[0].dedupKey, 'net.conn')
})

// (b) same dedupKey inside the rate window: both dispatch, second capture dropped.
test('rate window drops the second capture but never the dispatch', () => {
  const report = load()
  report.init({ dsn: VALID_DSN })
  assert.equal(report.isReady(), true)
  report.reportError('poll failed on stale token', new Error('e1'), { area: 'poll' }, 'poll.stale')
  report.reportError('poll failed on stale token', new Error('e2'), { area: 'poll' }, 'poll.stale')
  assert.equal(dispatched.length, 2, 'every event reaches the user lane')
  assert.equal(captured.length, 1, 'duplicate capture suppressed within the window')
})

// (D-5) re-entrancy guard: a throwing tackbox:error listener surfaces via
// window.onerror, which an app-owned global handler turns back into reportError ->
// dispatch on the same stack. The guard skips the nested dispatch so it cannot
// loop; sequential dispatches are unaffected.
test('a re-entering dispatch is skipped (no infinite loop, one outer dispatch)', () => {
  const report = load()
  report.init({})
  let calls = 0
  const realWindow = window
  Object.defineProperty(globalThis, 'window', {
    value: {
      addEventListener() {},
      dispatchEvent(ev) {
        calls++
        dispatched.push(ev.detail)
        // the listener failure re-enters synchronously via reportError
        report.reportError('re-entry from a listener failure', new Error('inner'), null, 'reentry.key')
        return true
      },
    },
    configurable: true,
    writable: true,
  })
  try {
    report.reportError('outer dispatch that re-enters', new Error('outer'), null, 'outer.key')
    assert.equal(calls, 1, 'exactly one dispatch; the nested re-entry is skipped')
    assert.equal(dispatched.length, 1, 'only the outer notice reaches the user lane')
  } finally {
    Object.defineProperty(globalThis, 'window', { value: realWindow, configurable: true, writable: true })
  }
})

// (c) reportPanic routes to the user lane with level fatal and a per-name key.
test('reportPanic dispatches level fatal with panic:<name> dedupKey', () => {
  const report = load()
  report.reportPanic('worker', new Error('kaboom'))
  assert.equal(dispatched.length, 1)
  const d = dispatched[0]
  assert.equal(d.level, 'fatal')
  assert.equal(d.dedupKey, 'panic:worker')
  assert.equal(d.msg, 'panic in worker')
  assert.equal(d.cause.message, 'kaboom')
})

// (d) capture builds Error(msg) with the original error chained as .cause (seed shape).
test('capture is Error(msg) with the original cause chained', () => {
  const report = load()
  report.init({ dsn: VALID_DSN })
  const original = new Error('socket hangup')
  report.reportError('upload failed mid-flight', original, { area: 'upload' }, 'upload.fail')
  assert.equal(captured.length, 1)
  const { err, level, fingerprint, tags } = captured[0]
  assert.ok(err instanceof Error)
  assert.equal(err.message, 'upload failed mid-flight')
  assert.equal(err.cause, original)
  assert.equal(level, 'error')
  assert.deepEqual(fingerprint, ['upload.fail'])
  assert.equal(tags.area, 'upload')
})

// (e) reportSynthError captures a plain Error(msg) with no cause chain.
test('reportSynthError captures Error(msg) with no cause', () => {
  const report = load()
  report.init({ dsn: VALID_DSN })
  report.reportSynthError('non-OK response handled inline', { area: 'http' }, 'http.synth')
  assert.equal(captured.length, 1)
  assert.equal(captured[0].err.message, 'non-OK response handled inline')
  assert.equal(captured[0].err.cause, undefined)
  assert.equal(dispatched.length, 1)
  assert.equal(dispatched[0].cause, null, 'synth user-lane detail carries cause: null')
})

// (f) reportQuiet captures at warning with no user-lane dispatch.
test('reportQuiet captures warning-level with no user-lane dispatch', () => {
  const report = load()
  report.init({ dsn: VALID_DSN })
  report.reportQuiet('index rebuild degraded, using stale', new Error('timeout'), { area: 'idx' }, 'idx.stale')
  assert.equal(captured.length, 1, 'quiet still captures')
  assert.equal(captured[0].level, 'warning')
  assert.deepEqual(captured[0].fingerprint, ['idx.stale'])
  assert.equal(dispatched.length, 0, 'quiet must not touch the user lane')
})

// (g) notify dispatches only, captures nothing, and consumes no rate slot.
test('notify dispatches level notice, captures nothing, leaves the rate slot', () => {
  const report = load()
  report.init({ dsn: VALID_DSN })
  report.notify('you appear to be offline', new Error('net down'), { area: 'conn' }, 'conn.offline')
  assert.equal(dispatched.length, 1)
  assert.equal(dispatched[0].level, 'notice')
  assert.equal(captured.length, 0, 'notify captures nothing')
  // Same dedupKey still captures: notify consumed no rate slot.
  report.reportError('still offline after retry', new Error('net down'), { area: 'conn' }, 'conn.offline')
  assert.equal(captured.length, 1, 'following reportError on the notify key still captures')
  assert.equal(dispatched.length, 2)
})

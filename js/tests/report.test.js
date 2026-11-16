const { test, before, after } = require('node:test')
const assert = require('node:assert')
const http = require('node:http')
const path = require('node:path')
const zlib = require('node:zlib')
const { pathToFileURL } = require('node:url')

// Both variants run on their real Sentry SDK against a local ingest endpoint, so
// the asserted events are what the SDK actually ships. A query-string import
// gives every scenario a fresh variant instance (pristine ready/rate state).

const logged = []
console.log = () => {}
console.warn = (...args) => logged.push(args.join(' '))
console.error = () => {}

const events = []
let server
let dsn

before(async () => {
  server = http.createServer((req, res) => {
    const chunks = []
    req.on('data', chunk => chunks.push(chunk))
    req.on('end', () => {
      let body = Buffer.concat(chunks)
      if (req.headers['content-encoding'] === 'gzip') body = zlib.gunzipSync(body)
      // envelope: a header line, then item-header / payload line pairs
      const lines = body.toString('utf8').split('\n')
      for (let i = 1; i + 1 < lines.length; i += 2) {
        if (JSON.parse(lines[i]).type === 'event') events.push(JSON.parse(lines[i + 1]))
      }
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end('{}')
    })
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  dsn = `http://public@127.0.0.1:${server.address().port}/1`
})

after(() => new Promise(resolve => server.close(resolve)))

let loads = 0

async function fresh(variant) {
  events.length = 0
  logged.length = 0
  const file = path.join(__dirname, '..', 'report', variant + '.mjs')
  return import(pathToFileURL(file).href + '?load=' + ++loads)
}

// Each loader returns a fresh report module plus the notices its user lane
// delivered; `sink` swaps in a custom user-lane sink for the failure scenarios.
const VARIANTS = {
  async browser(sink) {
    const notices = []
    globalThis.window = new EventTarget()
    window.addEventListener('tackbox:error', e => (sink ? sink(e.detail) : notices.push(e.detail)))
    return { report: await fresh('browser'), notices }
  },
  async node(sink) {
    delete globalThis.window
    const notices = []
    const report = await fresh('node')
    report.setNotifier(sink || (n => notices.push(n)))
    return { report, notices }
  },
}

const SHARED_VERBS = [
  'flush', 'init', 'isReady', 'notify', 'reportError', 'reportPanic',
  'reportQuiet', 'reportSynthError', 'reportWarn', 'verify',
]

function keyed(key) {
  return events.filter(e => e.fingerprint && e.fingerprint[0] === key)
}

function messages(event) {
  return event.exception.values.map(v => v.value)
}

for (const [name, load] of Object.entries(VARIANTS)) {
  test(`${name}: user lane delivers without a DSN, capture stays off`, async () => {
    const { report, notices } = await load()
    report.init({ silentMissing: true })
    assert.equal(report.isReady(), false)
    const cause = new Error('boom')
    report.reportError('connection lost mid-stream', cause, { area: 'net' }, 'net.conn')
    assert.deepEqual(notices, [
      { msg: 'connection lost mid-stream', cause, tags: { area: 'net' }, dedupKey: 'net.conn', level: 'error' },
    ])
    assert.equal(await report.verify(500), false, 'verify before a successful init')
    await report.flush(500)
    assert.equal(events.length, 0)
  })

  test(`${name}: D005 rate window drops the repeat capture, never the notice`, async () => {
    const { report, notices } = await load()
    report.init({ dsn })
    assert.equal(report.isReady(), true)
    report.reportError('poll failed on stale token', new Error('e1'), { area: 'poll' }, 'poll.stale')
    report.reportError('poll failed on stale token', new Error('e2'), { area: 'poll' }, 'poll.stale')
    await report.flush(2000)
    assert.equal(notices.length, 2, 'every event reaches the user lane')
    assert.equal(keyed('poll.stale').length, 1, 'in-window repeat capture dropped')
  })

  test(`${name}: a capture is Error(msg) chaining the caught cause, with level, tags, fingerprint`, async () => {
    const { report } = await load()
    report.init({ dsn, environment: 'test', release: 'r1' })
    report.reportError('upload failed mid-flight', new Error('socket hangup'), { area: 'upload' }, 'upload.fail')
    await report.flush(2000)
    const [event] = keyed('upload.fail')
    assert.equal(event.level, 'error')
    assert.equal(event.environment, 'test')
    assert.equal(event.release, 'r1')
    assert.equal(event.tags.area, 'upload')
    const values = event.exception.values
    assert.equal(values.at(-1).value, 'upload failed mid-flight', 'the wrapper is the primary exception')
    assert.ok(messages(event).includes('socket hangup'), 'the caught cause ships as a linked exception')
  })

  test(`${name}: D003 each capture owns its scope - no tag or fingerprint bleed`, async () => {
    const { report } = await load()
    report.init({ dsn })
    report.reportError('first failure in batch one', new Error('a'), { area: 'one' }, 'batch.one')
    report.reportWarn('second failure in batch two', new Error('b'), { zone: 'two' }, 'batch.two')
    report.reportSynthError('third failure without cause', null, 'batch.three')
    await report.flush(2000)
    const [one] = keyed('batch.one')
    const [two] = keyed('batch.two')
    const [three] = keyed('batch.three')
    assert.equal(one.level, 'error')
    assert.equal(two.level, 'warning')
    assert.deepEqual([one.tags.area, one.tags.zone], ['one', undefined])
    assert.deepEqual([two.tags.area, two.tags.zone], [undefined, 'two'])
    assert.deepEqual(three.tags || {}, {})
  })

  test(`${name}: D002 panics key per name`, async () => {
    const { report, notices } = await load()
    report.init({ dsn })
    report.reportPanic('worker', new Error('kaboom'))
    report.reportPanic('poller', 'not an error')
    report.reportPanic('worker', new Error('kaboom again'))
    await report.flush(2000)
    assert.deepEqual(notices.map(n => [n.level, n.dedupKey, n.msg]), [
      ['fatal', 'panic:worker', 'panic in worker'],
      ['fatal', 'panic:poller', 'panic in poller'],
      ['fatal', 'panic:worker', 'panic in worker'],
    ])
    const [worker] = keyed('panic:worker')
    const [poller] = keyed('panic:poller')
    assert.equal(keyed('panic:worker').length, 1, 'same name inside the window collapses')
    assert.equal(worker.level, 'fatal')
    assert.equal(worker.tags.source, 'worker')
    assert.deepEqual(messages(poller), ['not an error'])
  })

  test(`${name}: reportSynthError captures a plain Error(msg)`, async () => {
    const { report, notices } = await load()
    report.init({ dsn })
    report.reportSynthError('non-OK response handled inline', { area: 'http' }, 'http.synth')
    await report.flush(2000)
    assert.deepEqual(messages(keyed('http.synth')[0]), ['non-OK response handled inline'])
    assert.equal(notices[0].cause, null)
  })

  test(`${name}: reportQuiet captures at warning without the user lane`, async () => {
    const { report, notices } = await load()
    report.init({ dsn })
    report.reportQuiet('index rebuild degraded, using stale', new Error('timeout'), { area: 'idx' }, 'idx.stale')
    await report.flush(2000)
    assert.equal(keyed('idx.stale')[0].level, 'warning')
    assert.equal(notices.length, 0)
  })

  test(`${name}: notify reaches the user lane only and leaves the rate slot`, async () => {
    const { report, notices } = await load()
    report.init({ dsn })
    report.notify('you appear to be offline', new Error('net down'), { area: 'conn' }, 'conn.offline')
    report.reportError('still offline after retry', new Error('net down'), { area: 'conn' }, 'conn.offline')
    await report.flush(2000)
    assert.deepEqual(notices.map(n => n.level), ['notice', 'error'])
    assert.equal(keyed('conn.offline').length, 1, 'the following reportError still captures')
  })

  test(`${name}: verify ships the report.startup healthcheck`, async () => {
    const { report } = await load()
    report.init({ dsn })
    assert.equal(await report.verify(2000), true)
    const [event] = keyed('report.startup')
    assert.equal(event.level, 'info')
    assert.equal(event.tags.healthcheck, 'true')
    // GlitchTip titles an event by its exception type when one is attached, so the check must stay a plain message.
    assert.equal(event.message, 'report.Verify')
    assert.equal(event.exception, undefined)
  })

  test(`${name}: a sink that re-enters a verb is skipped, not looped`, async () => {
    const delivered = []
    const { report } = await load(n => {
      delivered.push(n.dedupKey)
      report.reportError('re-entry from the user-lane sink', new Error('inner'), null, 'reentry.inner')
    })
    report.init({ dsn })
    report.reportError('outer dispatch that re-enters', new Error('outer'), null, 'reentry.outer')
    await report.flush(2000)
    assert.deepEqual(delivered, ['reentry.outer'])
    assert.ok(logged.some(l => l.includes('nested user-lane dispatch skipped')))
    assert.equal(keyed('reentry.inner').length, 1, 'the nested call still captures')
  })

  test(`${name}: exports exactly the documented verbs`, async () => {
    const { report } = await load()
    const expected = name === 'node' ? [...SHARED_VERBS, 'setNotifier'].sort() : SHARED_VERBS
    assert.deepEqual(Object.keys(report), expected)
  })
}

test('node: a throwing notifier is captured on the quiet lane, never thrown to the caller', async () => {
  const { report } = await VARIANTS.node(() => {
    throw new Error('notifier is broken')
  })
  report.init({ dsn })
  report.reportError('caller path survives the sink', new Error('x'), null, 'sink.sync')
  await report.flush(2000)
  const [event] = keyed('report.notifier')
  assert.equal(event.level, 'warning')
  assert.ok(messages(event).includes('notifier is broken'))
  assert.equal(keyed('sink.sync').length, 1)
})

test('node: a rejecting async notifier is captured on the quiet lane', async () => {
  const { report } = await VARIANTS.node(async () => {
    throw new Error('notifier rejected')
  })
  report.init({ dsn })
  report.notify('you appear to be offline', new Error('net down'), null, 'conn.offline')
  await new Promise(resolve => setImmediate(resolve))
  await report.flush(2000)
  assert.ok(messages(keyed('report.notifier')[0]).includes('notifier rejected'))
})

test('node: setNotifier(null) clears the sink; a non-function is refused', async () => {
  const { report, notices } = await VARIANTS.node()
  report.setNotifier(null)
  report.notify('you appear to be offline', null, null, 'conn.offline')
  assert.equal(notices.length, 0)
  assert.throws(() => report.setNotifier(undefined), TypeError)
  assert.throws(() => report.setNotifier('notifier'), TypeError)
})

test('browser: a throwing window.dispatchEvent is captured on the quiet lane', async () => {
  const { report } = await VARIANTS.browser()
  globalThis.window = {
    dispatchEvent() {
      throw new Error('dispatch is broken')
    },
  }
  report.init({ dsn })
  report.reportWarn('caller path survives dispatch', new Error('x'), null, 'sink.dispatch')
  await report.flush(2000)
  assert.ok(messages(keyed('report.notifier')[0]).includes('dispatch is broken'))
  assert.equal(keyed('sink.dispatch').length, 1)
})

test('browser: without a window (SSR) the user lane is a no-op and capture still runs', async () => {
  const { report } = await VARIANTS.browser()
  delete globalThis.window
  report.init({ dsn })
  report.reportError('server-side render path failed', new Error('ssr'), null, 'ssr.render')
  await report.flush(2000)
  assert.equal(keyed('ssr.render').length, 1)
})

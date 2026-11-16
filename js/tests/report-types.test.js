const { test, before, after } = require('node:test')
const assert = require('node:assert')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const ts = require('typescript')

// Consumers outside the repo resolve tackbox/report/* through the package
// exports, as an installed dependency would: a temp project links
// node_modules/tackbox to this checkout. Fixtures are generated here, never
// committed (self-lint would scan them).

const REPO = path.join(__dirname, '..', '..')
const REPORT_DIR = path.join(REPO, 'js', 'report')

let root
let link

before(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'tbx-report-types-'))
  fs.mkdirSync(path.join(root, 'node_modules'))
  link = path.join(root, 'node_modules', 'tackbox')
  fs.symlinkSync(REPO, link, 'junction')
})

after(() => {
  // Drop the link first so the recursive removal can never walk into the checkout.
  fs.unlinkSync(link)
  fs.rmSync(root, { recursive: true, force: true })
})

// The server profile mirrors a strict Node 26 ESM app that runs .ts directly.
const SERVER = {
  target: 'es2024',
  lib: ['es2024'],
  module: 'nodenext',
  moduleResolution: 'nodenext',
  types: [],
  strict: true,
  noUncheckedIndexedAccess: true,
  exactOptionalPropertyTypes: true,
  noPropertyAccessFromIndexSignature: true,
  verbatimModuleSyntax: true,
  noEmit: true,
  skipLibCheck: true,
}

// The client profile mirrors a Vite/Svelte app.
const CLIENT = {
  ...SERVER,
  lib: ['es2024', 'dom'],
  module: 'esnext',
  moduleResolution: 'bundler',
}

function program(files, options) {
  const names = []
  for (const [rel, code] of Object.entries(files)) {
    const abs = path.join(root, rel)
    fs.writeFileSync(abs, code)
    names.push(abs)
  }
  const { options: parsed, errors } = ts.convertCompilerOptionsFromJson(options, root)
  assert.deepEqual(errors, [], 'fixture compiler options must parse')
  return ts.createProgram(names, parsed)
}

function diagnostics(prog) {
  return ts.getPreEmitDiagnostics(prog).map(d => {
    const where = d.file ? path.basename(d.file.fileName) + ':' + (d.file.getLineAndCharacterOfPosition(d.start).line + 1) : ''
    return `${where} TS${d.code} ${ts.flattenDiagnosticMessageText(d.messageText, ' ')}`
  })
}

const SERVER_TS = `
import { flush, init, isReady, notify, reportError, reportPanic, reportQuiet, reportSynthError, reportWarn, setNotifier, verify } from 'tackbox/report/node'
import type { InitOptions, Notice, NoticeLevel, Notifier, Tags } from 'tackbox/report/node'
import * as report from 'tackbox/report/node'

const optional: string | undefined = undefined
const opts: InitOptions = { dsn: optional, release: optional, environment: 'dev', rateWindow: 1000, integrations: [] }
init(opts)
init()
const onNotice: Notifier = (n: Notice) => {
  const level: NoticeLevel = n.level
  const tags: Tags | null = n.tags
  const msg: string = n.msg
  const key: string = n.dedupKey
  const cause: unknown = n.cause
  void [level, tags, msg, key, cause]
}
setNotifier(onNotice)
setNotifier(async () => {})
setNotifier(null)
try {
  JSON.parse('{')
} catch (err) {
  reportError('connection lost mid-stream', err, { area: 'net' }, 'net.lost')
}
reportWarn('connection lost mid-stream', null, null, 'net.lost')
reportQuiet('index rebuild degraded, using stale', new Error('x'), null, 'idx.stale')
notify('you appear to be offline', new Error('x'), { area: 'conn' }, 'conn.offline')
reportSynthError('retry budget exhausted at boot stage', null, 'boot.retry')
reportPanic('worker', 42)
const ready: boolean = isReady()
const verified: boolean = await verify()
const drained: void = await flush(2000)
await report.flush()
void [ready, verified, drained]
`

const CLIENT_TS = `
import { init, reportError, type Notice } from 'tackbox/report/browser'
import * as report from 'tackbox/report/browser'

init({ dsn: '', release: undefined, integrations: [] })
window.addEventListener('tackbox:error', event => {
  const notice: Notice = event.detail
  const key: string = event.detail.dedupKey
  void [notice, key]
})
reportError('connection lost mid-stream', new Error('x'), { area: 'ui' }, 'ui.lost')
report.reportPanic('render', new Error('x'))
`

const LEGACY_CTS = `
import report = require('tackbox/report/node')
import browser = require('tackbox/report/browser')

report.init({})
report.reportError('connection lost mid-stream', new Error('x'), null, 'net.lost')
browser.reportWarn('connection lost mid-stream', new Error('x'), null, 'net.lost')
`

test('ESM server, CJS, and bundler client consumers type-check against the shipped declarations', () => {
  assert.deepEqual(diagnostics(program({ 'server.mts': SERVER_TS, 'legacy.cts': LEGACY_CTS }, SERVER)), [])
  assert.deepEqual(diagnostics(program({ 'client.ts': CLIENT_TS }, CLIENT)), [])
})

test('misuse is a type error', () => {
  const server = diagnostics(program({
    'bad.mts': `
import { reportError, setNotifier } from 'tackbox/report/node'
import { setNotifier as browserNotifier } from 'tackbox/report/browser'
reportError('connection lost mid-stream', new Error('x'))
setNotifier('notifier')
void browserNotifier
`,
  }, SERVER))
  const client = diagnostics(program({
    'bad-client.ts': `
window.addEventListener('tackbox:error', event => {
  const level: number = event.detail.level
  void level
})
export {}
import 'tackbox/report/browser'
`,
  }, CLIENT))
  const codes = [...server, ...client].map(d => d.split(' ')[1]).sort()
  // 2305: setNotifier is Node-only; 2554: dedupKey and tags are required;
  // 2345: a notifier must be a function; 2322: Notice.level is a string union.
  assert.deepEqual(codes, ['TS2305', 'TS2322', 'TS2345', 'TS2554'], [...server, ...client].join('\n'))
})

test('the declarations themselves are error-free', () => {
  const prog = program({ 'server.mts': SERVER_TS, 'client.ts': CLIENT_TS }, { ...CLIENT, skipLibCheck: false })
  const own = prog.getSourceFiles().filter(sf => path.dirname(path.resolve(sf.fileName)) === REPORT_DIR)
  assert.deepEqual(own.map(sf => path.basename(sf.fileName)).sort(), ['api.d.mts', 'browser.d.mts', 'node.d.mts'])
  const errs = own.flatMap(sf => [...prog.getSyntacticDiagnostics(sf), ...prog.getSemanticDiagnostics(sf)])
  assert.deepEqual(errs.map(d => ts.flattenDiagnosticMessageText(d.messageText, ' ')), [])
})

// The declared value exports must be exactly the runtime exports of each variant.
for (const [variant, files, options] of [
  ['node', { 'server.mts': SERVER_TS }, SERVER],
  ['browser', { 'client.ts': CLIENT_TS }, CLIENT],
]) {
  test(`${variant}: declared value exports match the runtime module`, async () => {
    const prog = program(files, options)
    const checker = prog.getTypeChecker()
    const decl = prog.getSourceFiles().find(sf => path.resolve(sf.fileName) === path.join(REPORT_DIR, variant + '.d.mts'))
    const declared = checker.getExportsOfModule(checker.getSymbolAtLocation(decl))
      .filter(s => (s.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(s) : s).flags & ts.SymbolFlags.Value)
      .map(s => s.name)
      .sort()
    const runtime = Object.keys(await import('tackbox/report/' + variant)).sort()
    assert.deepEqual(declared, runtime)
  })
}

test('a CommonJS require and an ESM import share one module instance', async () => {
  for (const variant of ['browser', 'node']) {
    const cjs = require('tackbox/report/' + variant)
    const esm = await import('tackbox/report/' + variant)
    assert.equal(cjs.reportError, esm.reportError, `${variant}: no dual-package state split`)
  }
})

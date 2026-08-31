const { after, test } = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { PassThrough } = require('node:stream')
const { execFileSync } = require('node:child_process')
const { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } = require('node:fs')
const { tmpdir } = require('node:os')
const path = require('node:path')

const hook = require('../omp/hook')
const extension = require('../omp/index')
const manifest = require('../../package.json')

const DIR = mkdtempSync(path.join(tmpdir(), 'tackbox-omp-'))
let stubs = 0

after(() => rmSync(DIR, { recursive: true, force: true, maxRetries: 5, retryDelay: 20 }))

function stub(body) {
  const file = path.join(DIR, `stub-${stubs++}.cjs`)
  writeFileSync(file, body)
  return JSON.stringify([process.execPath, file])
}

function answering(kind, reason = '') {
  return stub(`process.stdin.resume(); process.stdout.write(${JSON.stringify(JSON.stringify({ protocol: 1, decision: kind, reason }))})`)
}

function commandFor(body) {
  return stub(body)
}

function localRepository(name) {
  const repo = path.join(DIR, `${name}-${stubs}`)
  mkdirSync(repo)
  writeFileSync(path.join(repo, 'dev.py'), '# hook fixture\n')
  execFileSync('git', ['init', '-q'], { cwd: repo })
  return repo
}

function localCliCommand() {
  return JSON.stringify([
    'uv', 'run', '--directory', path.join(process.cwd(), 'py'), 'python', '-m', 'tackbox.cli',
  ])
}
function childWithProtocolStreams() {
  const child = new EventEmitter()
  child.stdin = new EventEmitter()
  child.stdin.end = () => {}
  child.stdout = new PassThrough()
  child.stderr = new PassThrough()
  child.kill = () => true
  return child
}


function withCommand(command, callback) {
  const prior = process.env.TACKBOX_OMP_COMMAND
  process.env.TACKBOX_OMP_COMMAND = command
  return Promise.resolve()
    .then(callback)
    .finally(() => {
      if (prior === undefined) delete process.env.TACKBOX_OMP_COMMAND
      else process.env.TACKBOX_OMP_COMMAND = prior
    })
}

function load() {
  const handlers = new Map()
  const pi = {
    setLabel(label) {
      assert.equal(label, 'tackbox')
    },
    on(name, handler) {
      handlers.set(name, handler)
    },
  }
  extension(pi)
  return handlers
}

function context(options = {}) {
  const confirmations = []
  return {
    cwd: options.cwd || DIR,
    hasUI: options.hasUI !== false,
    ui: options.hasUI === false ? undefined : {
      async confirm(title, reason) {
        confirmations.push({ title, reason })
        return options.confirm === undefined ? true : options.confirm
      },
    },
    confirmations,
    setTimeout,
    clearTimer: clearTimeout,
  }
}

function preInput() {
  return { path: 'a.js', old_string: 'old', new_string: 'new' }
}

function detail(pathname = 'a.js') {
  return {
    path: pathname,
    sourcePath: pathname,
    op: 'update',
    move: false,
    diff: '@@\n-old\n+new\n',
    oldText: 'old',
    newText: 'new\n',
    snapshotsPruned: false,
  }
}

async function toolCall(command, input = preInput(), ctx = context(), toolName = 'edit') {
  const handlers = load()
  return withCommand(command, () => handlers.get('tool_call')({ toolName, input }, ctx))
}

async function toolResult(command, event = {}, ctx = context()) {
  const handlers = load()
  const full = {
    toolName: 'edit',
    input: preInput(),
    details: detail(),
    isError: false,
    content: [],
    ...event,
  }
  return withCommand(command, () => handlers.get('tool_result')(full, ctx))
}

function protocolEvent(phase = 'pre') {
  const normalized = {
    tool: 'edit',
    targets: [{ path: path.join(DIR, 'a.js'), op: 'edit', expectedPresent: true, added: ['new'], removed: ['old'] }],
    unknown: null,
    succeeded: true,
  }
  return hook.request(phase, DIR, normalized)
}

test('the default command pins the wheel to the npm package version', () => {
  assert.deepEqual(hook.resolveArgv({}, manifest.version), {
    value: ['uvx', `tackbox@${manifest.version}`, 'hook-protocol'],
    error: null,
  })
})

test('a development override is argv-only and malformed JSON is unverified', () => {
  assert.deepEqual(hook.resolveArgv({ TACKBOX_OMP_COMMAND: '["python","-m","tackbox.cli"]' }, manifest.version), {
    value: ['python', '-m', 'tackbox.cli', 'hook-protocol'], error: null,
  })
  assert.match(hook.resolveArgv({ TACKBOX_OMP_COMMAND: '{bad' }, manifest.version).error, /JSON array/)
})

test('response validation rejects bad version, kind, and reason', () => {
  assert.equal(hook.asDecision({ protocol: 1, decision: 'block', reason: 'no' }).kind, hook.BLOCK)
  for (const response of [
    { protocol: '1', decision: 'allow', reason: '' },
    { protocol: 2, decision: 'allow', reason: '' },
    { protocol: 1, decision: 'maybe', reason: 'x' },
    { protocol: 1, decision: 'block', reason: 7 },
    { protocol: 1, decision: 'allow', reason: 'unexpected' },
  ]) {
    assert.equal(hook.asDecision(response).kind, hook.UNVERIFIED)
  }
})

test('the spawned client sends strict post protocol fields', async () => {
  const record = path.join(DIR, `request-${stubs}.json`)
  const command = commandFor(`let input = ''; process.stdin.setEncoding('utf8'); process.stdin.on('data', chunk => { input += chunk }); process.stdin.on('end', () => { require('node:fs').writeFileSync(${JSON.stringify(record)}, input); process.stdout.write(${JSON.stringify(JSON.stringify({ protocol: 1, decision: 'allow', reason: '' }))}) })`)
  const decision = await withCommand(command, () => hook.decide(protocolEvent('post'), {
    env: process.env, version: manifest.version, timers: null,
  }))
  assert.equal(decision.kind, hook.ALLOW)
  const sent = JSON.parse(readFileSync(record, 'utf8'))
  assert.equal(sent.protocol, 1)
  assert.equal(sent.phase, 'post')
  assert.equal(sent.succeeded, true)
  assert.equal(sent.targets[0].expectedPresent, true)
})

test('spawn failure, nonzero child, and malformed child JSON are unverified', async () => {
  const failures = [
    JSON.stringify(['definitely-not-a-tackbox-executable']),
    commandFor("process.stderr.write('child failed\\n'); process.exit(4)"),
    commandFor("process.stdout.write('not json')"),
  ]
  for (const command of failures) {
    const decision = await hook.decide(protocolEvent(), {
      env: { ...process.env, TACKBOX_OMP_COMMAND: command },
      version: manifest.version,
      timers: null,
    })
    assert.equal(decision.kind, hook.UNVERIFIED)
    assert.match(decision.reason, /tackbox/)
  }
})

test('a child timeout is unverified without waiting for the production deadline', async () => {
  const command = commandFor('setInterval(() => {}, 1000)')
  const decision = await hook.decide(protocolEvent(), {
    env: { ...process.env, TACKBOX_OMP_COMMAND: command },
    version: manifest.version,
    timers: {
      set(fn) {
        return setTimeout(fn, 5)
      },
      clear(handle) {
        clearTimeout(handle)
      },
    },
  })
  assert.equal(decision.kind, hook.UNVERIFIED)
  assert.match(decision.reason, /timed out/)
})
test('protocol stream failures are unverified and EPIPE outranks a readable response', async () => {
  for (const stream of ['stdout', 'stderr']) {
    const child = childWithProtocolStreams()
    const run = hook.execute(['child'], protocolEvent(), { set: setTimeout, clear: clearTimeout }, () => child)
    child[stream].emit('error', new Error(`${stream} broke`))
    const result = await run
    assert.equal(result.code, null)
    assert.match(result.failure, new RegExp(`${stream} stream failed`))
  }
  const child = childWithProtocolStreams()
  const run = hook.execute(['child'], protocolEvent(), { set: setTimeout, clear: clearTimeout }, () => child)
  child.stdout.write(JSON.stringify({ protocol: 1, decision: 'allow', reason: '' }))
  child.emit('close', 0)
  const epipe = new Error('write EPIPE')
  epipe.code = 'EPIPE'
  child.stdin.emit('error', epipe)
  const result = await run
  assert.equal(result.code, null)
  assert.match(result.failure, /stdin stream failed.*EPIPE/)
})

test('the 20-second child deadline resolves before a shortened outer deadline', async () => {
  let childDeadline = null
  const command = commandFor('setInterval(() => {}, 1000)')
  const decision = hook.decide(protocolEvent(), {
    env: { ...process.env, TACKBOX_OMP_COMMAND: command },
    version: manifest.version,
    timers: {
      set(fn, ms) {
        childDeadline = ms
        return setTimeout(fn, 5)
      },
      clear(handle) {
        clearTimeout(handle)
      },
    },
  })
  const outer = new Promise(resolve => setTimeout(() => resolve('outer deadline'), 100))
  const outcome = await Promise.race([decision, outer])
  assert.notEqual(outcome, 'outer deadline')
  assert.equal(outcome.kind, hook.UNVERIFIED)
  assert.equal(childDeadline, hook.TIMEOUT_MS)
  assert.equal(hook.TIMEOUT_MS, 20000)
  assert.ok(hook.TIMEOUT_MS < 30000)
})


test('allow, block, confirmation, denial, and headless approval map through PRE', async () => {
  assert.equal(await toolCall(answering('allow')), undefined)
  const blocked = await toolCall(answering('block', 'policy violation'))
  assert.deepEqual(blocked, { block: true, reason: 'policy violation' })
  const approvedContext = context({ confirm: true })
  assert.equal(await toolCall(answering('ask', 'approve this'), preInput(), approvedContext), undefined)
  assert.equal(approvedContext.confirmations.length, 1)
  const denied = await toolCall(answering('ask', 'approve this'), preInput(), context({ confirm: false }))
  assert.match(denied.reason, /approval denied/)
  const headless = await toolCall(answering('ask', 'approve this'), preInput(), context({ hasUI: false }))
  assert.equal(headless.block, true)
  assert.match(headless.reason, /no interactive session/)
})

test('every PRE infrastructure and malformed-host path blocks instead of warning', async () => {
  const paths = [
    () => toolCall(answering('warn', 'wheel unavailable')),
    () => toolCall(JSON.stringify(['definitely-not-a-tackbox-executable'])),
    () => toolCall(commandFor("process.stdout.write('bad json')")),
    () => toolCall('{not argv'),
    () => toolCall(answering('allow'), preInput(), { cwd: '' }),
  ]
  for (const run of paths) {
    const result = await run()
    assert.equal(result.block, true)
    assert.match(result.reason, /tackbox/)
  }
})

test('opaque write, bash, and eval keep the accepted target-free PRE residual', async () => {
  const never = JSON.stringify(['definitely-not-a-tackbox-executable'])
  assert.equal(await toolCall(never, { path: 'xd://lsp', content: '{}' }, context(), 'write'), undefined)
  assert.equal(await toolCall(never, { command: 'true' }, context(), 'bash'), undefined)
  assert.equal(await toolCall(never, { code: 'x = 1' }, context(), 'eval'), undefined)
})

test('a known tool with unknown payload reaches the core and blocks on its refusal', async () => {
  const result = await toolCall(answering('block', 're-issue documented form'), {})
  assert.deepEqual(result, { block: true, reason: 're-issue documented form' })
})

test('a post violation appends findings and makes the tool result an error', async () => {
  const result = await toolResult(answering('block', 'a.js:1: TBX001: fix it'), {
    content: [{ type: 'text', text: 'original result' }],
  })
  assert.equal(result.isError, true)
  assert.deepEqual(result.content, [
    { type: 'text', text: 'original result' },
    { type: 'text', text: 'tackbox blocked this change:\na.js:1: TBX001: fix it' },
  ])
})

test('a post infrastructure failure preserves successful result state and forbids repetition', async () => {
  const result = await toolResult(answering('warn', 'engine store missing'))
  assert.equal(result.isError, undefined)
  const text = result.content.at(-1).text
  assert.match(text, /mutation may already have landed/)
  assert.match(text, /verification did not complete.*engine store missing/s)
  assert.match(text, /Do not repeat the mutation; dev.py check remains required/)
})

test('a post local infrastructure failure omits an override so the wrapper keeps its error', async () => {
  const result = await toolResult(JSON.stringify(['definitely-not-a-tackbox-executable']), {
    isError: true,
    details: undefined,
    content: [{ type: 'text', text: 'tool failed first' }],
  })
  assert.equal(Object.hasOwn(result, 'isError'), false)
  assert.equal(result.isError ?? true, true)
  assert.equal(result.content[0].text, 'tool failed first')
  assert.match(result.content[1].text, /must not repeat|Do not repeat/)
})

test('an unrecognized post decision becomes an unverified warning', async () => {
  const result = await toolResult(answering('ask', 'unexpected post approval'), {
    content: [{ type: 'text', text: 'original result' }],
  })
  assert.equal(result.isError, undefined)
  const text = result.content.at(-1).text
  assert.match(text, /unrecognized post decision ask/)
  assert.match(text, /mutation may already have landed/)
  assert.match(text, /Do not repeat the mutation; dev.py check remains required/)
})


test('a failed edit result does not become a missing-target verification error', async () => {
  const result = await toolResult(answering('allow'), {
    isError: true,
    details: undefined,
    content: [{ type: 'text', text: 'original failure' }],
  })
  assert.equal(result, undefined)
})

test('the post adapter uses authoritative OMP 18.0.11 details before input', async () => {
  const record = path.join(DIR, `details-${stubs}.json`)
  const command = commandFor(`let input = ''; process.stdin.setEncoding('utf8'); process.stdin.on('data', chunk => { input += chunk }); process.stdin.on('end', () => { require('node:fs').writeFileSync(${JSON.stringify(record)}, input); process.stdout.write(${JSON.stringify(JSON.stringify({ protocol: 1, decision: 'allow', reason: '' }))}) })`)
  const result = await toolResult(command, {
    input: { path: 'input.js', old_string: 'old', new_string: 'wrong' },
    details: detail('actual.js'),
  })
  assert.equal(result, undefined)
  const request = JSON.parse(readFileSync(record, 'utf8'))
  assert.equal(request.succeeded, true)
  assert.equal(request.targets[0].path, path.resolve(DIR, 'actual.js'))
  assert.equal(request.targets[0].content, 'new\n')
})

test('a thrown aggregate result still scopes successful per-file details', async () => {
  const record = path.join(DIR, `partial-${stubs}.json`)
  const command = commandFor(`let input = ''; process.stdin.setEncoding('utf8'); process.stdin.on('data', chunk => { input += chunk }); process.stdin.on('end', () => { require('node:fs').writeFileSync(${JSON.stringify(record)}, input); process.stdout.write(${JSON.stringify(JSON.stringify({ protocol: 1, decision: 'allow', reason: '' }))}) })`)
  const result = await toolResult(command, {
    isError: true,
    details: {
      perFileResults: [
        { path: 'landed.js', op: 'update', newText: 'const landed = true\n', snapshotsPruned: false },
        { path: 'failed.js', isError: true, errorText: 'hashline mismatch' },
      ],
    },
  })
  assert.equal(result, undefined)
  const request = JSON.parse(readFileSync(record, 'utf8'))
  assert.equal(request.succeeded, false)
  assert.deepEqual(request.targets, [{
    path: path.resolve(DIR, 'landed.js'),
    op: 'edit',
    expectedPresent: true,
    content: 'const landed = true\n',
  }])
})
test('pathless noop details invoke the target-free post wall', async () => {
  const record = path.join(DIR, `noop-${stubs}.json`)
  const command = commandFor(`let input = ''; process.stdin.setEncoding('utf8'); process.stdin.on('data', chunk => { input += chunk }); process.stdin.on('end', () => { require('node:fs').writeFileSync(${JSON.stringify(record)}, input); process.stdout.write(${JSON.stringify(JSON.stringify({ protocol: 1, decision: 'allow', reason: '' }))}) })`)
  const result = await toolResult(command, {
    input: { input: '[input.js#ABCD]\nPUT 1.=1:\n+new\n' },
    details: { op: 'update', diff: '', snapshotsPruned: false },
  })
  assert.equal(result, undefined)
  const request = JSON.parse(readFileSync(record, 'utf8'))
  assert.equal(request.phase, 'post')
  assert.deepEqual(request.targets, [])
  assert.equal(request.targetless, 'no-op')
})


test('the real JavaScript adapter round trips through the local Python CLI', async () => {
  const repo = localRepository('roundtrip')
  const command = localCliCommand()
  const result = await toolCall(command, { path: 'a.py', content: 'x = 1\n' }, context({ cwd: repo }), 'write')
  assert.equal(result, undefined)
})
test('post write and pruned move details round trip through the local Python CLI', async () => {
  const repo = localRepository('post-roundtrip')
  writeFileSync(path.join(repo, 'written.js'), 'const written = true\n')
  writeFileSync(path.join(repo, 'moved.js'), 'const moved = true\n')
  const command = localCliCommand()
  const writeResult = await toolResult(command, {
    toolName: 'write',
    input: { path: 'input.js', content: 'wrong\n' },
    details: {
      resolvedPath: path.join(repo, 'written.js'),
      madeExecutable: false,
      diagnostics: { messages: [] },
      meta: {},
    },
  }, context({ cwd: repo }))
  assert.equal(writeResult, undefined)
  const moveResult = await toolResult(command, {
    input: {
      input: '*** Begin Patch\n*** Delete File: gone.js\n*** Update File: old.js\n*** Move to: moved.js\n@@\n-const old = true\n+const moved = true\n*** End Patch\n',
    },
    details: {
      snapshotsPruned: true,
      perFileResults: [
        { path: 'gone.js', sourcePath: 'gone.js', op: 'delete' },
        { path: 'moved.js', sourcePath: 'old.js', op: 'update', move: true },
      ],
    },
  }, context({ cwd: repo }))
  assert.equal(moveResult, undefined)
})

test('the shipped manifest declares the public OMP extension', () => {
  assert.deepEqual(manifest.omp.extensions, ['./js/omp/index.mjs'])
  assert.equal(existsSync(path.join(process.cwd(), manifest.omp.extensions[0])), true)
})

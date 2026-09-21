'use strict'

const assert = require('node:assert/strict')
const { existsSync } = require('node:fs')
const { spawnSync } = require('node:child_process')
const path = require('node:path')
const { test } = require('node:test')

const protocol = require('../omp/hook')
const manifest = require('../../package.json')
const {
  APPROVAL_UNAVAILABLE,
  handle,
  requestFromEvent,
} = require('../codex/hook')
const BIN = path.resolve(__dirname, '..', '..', 'bin', 'tackbox-codex-hook.js')

const CWD = path.resolve(path.sep === '\\' ? 'C:/repo' : '/repo')

function event(name, command) {
  return {
    hook_event_name: name,
    tool_name: 'apply_patch',
    cwd: CWD,
    tool_input: { command },
  }
}

const PATCH = [
  '*** Begin Patch',
  '*** Update File: src/app.ts',
  '@@',
  '-old()',
  '+updated()',
  '*** End Patch',
  '',
].join('\n')

test('Codex apply_patch becomes a strict protocol request', () => {
  const result = requestFromEvent(event('PreToolUse', PATCH))
  assert.equal(result.failure, undefined)
  assert.deepEqual(result.request, {
    protocol: 1,
    phase: 'pre',
    cwd: CWD,
    tool: 'apply_patch',
    targets: [{
      path: path.resolve(CWD, 'src/app.ts'),
      op: 'edit',
      expectedPresent: true,
      added: ['updated()'],
      removed: [],
    }],
    unknown: null,
  })
})

test('Codex post request marks the mutation as landed', () => {
  const result = requestFromEvent(event('PostToolUse', PATCH))
  assert.equal(result.request.phase, 'post')
  assert.equal(result.request.succeeded, true)
})

test('approval-required pre edit fails closed because Codex cannot ask', async () => {
  const result = await handle(event('PreToolUse', PATCH), {
    decide: async () => ({ kind: protocol.ASK, reason: 'approve marker' }),
  })
  const output = JSON.parse(result.stdout)
  assert.equal(result.code, 0)
  assert.equal(output.decision, 'block')
  assert.match(output.reason, /approve marker/)
  assert.match(output.reason, new RegExp(APPROVAL_UNAVAILABLE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
})

test('unverified pre edit fails closed', async () => {
  const result = await handle(event('PreToolUse', PATCH), {
    decide: async () => ({ kind: protocol.UNVERIFIED, reason: 'verification failed' }),
  })
  assert.deepEqual(JSON.parse(result.stdout), {
    decision: 'block',
    reason: 'verification failed',
  })
})

test('post violation replaces the Codex tool result with findings', async () => {
  const result = await handle(event('PostToolUse', PATCH), {
    decide: async () => ({ kind: protocol.BLOCK, reason: 'src/app.ts:1: finding' }),
  })
  assert.deepEqual(JSON.parse(result.stdout), {
    decision: 'block',
    reason: 'src/app.ts:1: finding',
  })
})

test('unverified post edit warns against repeating a possibly landed mutation', async () => {
  const result = await handle(event('PostToolUse', PATCH), {
    decide: async () => ({ kind: protocol.UNVERIFIED, reason: 'verification failed' }),
  })
  const output = JSON.parse(result.stdout)
  assert.equal(output.hookSpecificOutput.hookEventName, 'PostToolUse')
  assert.match(output.hookSpecificOutput.additionalContext, /mutation may already have landed/)
  assert.match(output.hookSpecificOutput.additionalContext, /verification failed/)
  assert.match(output.hookSpecificOutput.additionalContext, /Do not repeat the mutation/)
  assert.match(output.hookSpecificOutput.additionalContext, /dev\.py check remains required/)
})

test('malformed apply_patch fails closed before execution', async () => {
  const result = await handle(event('PreToolUse', 'not a patch'), {
    decide: async request => {
      assert.match(request.unknown, /cannot classify/)
      return { kind: protocol.BLOCK, reason: request.unknown }
    },
  })
  const output = JSON.parse(result.stdout)
  assert.equal(output.decision, 'block')
  assert.match(output.reason, /cannot classify/)
})

test('unrelated Codex events bypass Tackbox without running a decision', async () => {
  let called = false
  const result = await handle({
    ...event('PreToolUse', PATCH),
    tool_name: 'shell',
  }, {
    decide: async () => {
      called = true
      return { kind: protocol.BLOCK, reason: 'unexpected' }
    },
  })
  assert.deepEqual(result, { code: 0, stdout: '', stderr: '' })
  assert.equal(called, false)
})

test('malformed matched Codex events fail closed before execution', async () => {
  for (const malformed of [
    null,
    { ...event('PreToolUse', PATCH), cwd: 'relative' },
    { ...event('PreToolUse', PATCH), tool_input: { command: 1 } },
    event('PreToolUse', '   '),
  ]) {
    const result = await handle(malformed)
    const output = JSON.parse(result.stdout)
    assert.equal(output.decision, 'block')
    assert.equal(typeof output.reason, 'string')
    assert.notEqual(output.reason, '')
  }
})

test('unknown decisions fail closed before a patch and warn safely after it', async () => {
  const decide = async () => ({ kind: 'future' })
  const pre = JSON.parse((await handle(event('PreToolUse', PATCH), { decide })).stdout)
  assert.equal(pre.decision, 'block')
  assert.match(pre.reason, /unrecognized pre decision future/)

  const post = JSON.parse((await handle(event('PostToolUse', PATCH), { decide })).stdout)
  assert.equal(post.hookSpecificOutput.hookEventName, 'PostToolUse')
  assert.match(post.hookSpecificOutput.additionalContext, /mutation may already have landed/)
  assert.match(post.hookSpecificOutput.additionalContext, /unrecognized post decision future/)
  assert.match(post.hookSpecificOutput.additionalContext, /Do not repeat the mutation/)
})

test('the executable blocks an unreadable Codex event', () => {
  const result = spawnSync(process.execPath, [BIN], {
    input: 'not json',
    encoding: 'utf8',
  })
  assert.equal(result.status, 2)
  assert.match(result.stderr, /unreadable stdin/)
})

test('the executable maps unexpected synchronous and asynchronous failures to exit 2', () => {
  const hookPath = JSON.stringify(require.resolve('../codex/hook'))
  const binPath = JSON.stringify(BIN)
  const mains = [
    "() => { throw new Error('forced synchronous failure') }",
    "() => Promise.reject(new Error('forced asynchronous failure'))",
  ]
  for (const main of mains) {
    const source = `require.cache[${hookPath}] = {exports: {main: ${main}}}; require(${binPath})`
    const result = spawnSync(process.execPath, ['-e', source], { encoding: 'utf8' })
    assert.equal(result.status, 2)
    assert.match(result.stderr, /forced (?:synchronous|asynchronous) failure/)
  }
})

test('the executable completes an allowed Codex hook', () => {
  const allow = "process.stdout.write(JSON.stringify({protocol:1,decision:'allow',reason:''}))"
  const input = event('PreToolUse', PATCH)
  input.cwd = process.cwd()
  const result = spawnSync(process.execPath, [BIN], {
    input: JSON.stringify(input),
    encoding: 'utf8',
    env: {
      ...process.env,
      [protocol.COMMAND_ENV]: JSON.stringify([process.execPath, '-e', allow]),
    },
  })
  assert.equal(result.status, 0)
  assert.equal(result.stdout, '')
  assert.equal(result.stderr, '')
})

test('the shipped manifest declares the Codex hook executable', () => {
  assert.equal(manifest.bin['tackbox-codex-hook'], './bin/tackbox-codex-hook.js')
  assert.equal(existsSync(path.join(process.cwd(), manifest.bin['tackbox-codex-hook'])), true)
})

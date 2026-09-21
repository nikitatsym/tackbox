'use strict'

const fs = require('node:fs')
const path = require('node:path')
const manifest = require('../../package.json')
const protocol = require('../omp/hook')
const payload = require('../omp/payload')

const PRE = 'PreToolUse'
const POST = 'PostToolUse'
const APPLY_PATCH = 'apply_patch'
const APPROVAL_UNAVAILABLE =
  'Codex hooks cannot present a Tackbox approval prompt, so this edit is blocked. ' +
  'Make the approval-gated change in an interactive host that supports the prompt.'

function requestFromEvent(event) {
  if (event === null || typeof event !== 'object' || Array.isArray(event)) {
    return { failure: 'Codex supplied a non-object hook event' }
  }
  const phase = event.hook_event_name === PRE
    ? 'pre'
    : event.hook_event_name === POST
      ? 'post'
      : null
  if (phase === null) return { ignored: true }
  if (event.tool_name !== APPLY_PATCH) return { ignored: true }
  if (typeof event.cwd !== 'string' || !path.isAbsolute(event.cwd)) {
    return { phase, failure: 'Codex supplied no absolute cwd' }
  }
  if (
    event.tool_input === null ||
    typeof event.tool_input !== 'object' ||
    Array.isArray(event.tool_input) ||
    typeof event.tool_input.command !== 'string'
  ) {
    return { phase, failure: 'Codex supplied apply_patch without a string tool_input.command' }
  }
  if (event.tool_input.command.trim() === '') {
    return { phase, failure: 'Codex supplied an empty apply_patch command' }
  }
  const normalized = payload.normalize(
    APPLY_PATCH,
    { input: event.tool_input.command },
    event.cwd,
  )
  if (normalized === null) return { ignored: true }
  if (normalized.failure) return { phase, failure: normalized.failure }
  if (phase === 'post') normalized.succeeded = true
  return {
    phase,
    request: protocol.request(phase, event.cwd, normalized),
  }
}

async function handle(event, options = {}) {
  const built = requestFromEvent(event)
  if (built.ignored) return { code: 0, stdout: '', stderr: '' }
  const phase = built.phase || 'pre'
  const decision = built.failure
    ? protocol.unverified(built.failure)
    : await (options.decide || protocol.decide)(built.request, {
        env: options.env || process.env,
        version: options.version || manifest.version,
        timers: options.timers,
      })
  return render(phase, decision)
}

function render(phase, decision) {
  const kind = decision && decision.kind
  if (kind === protocol.ALLOW) return { code: 0, stdout: '', stderr: '' }
  if (phase === 'pre') {
    let reason
    if (kind === protocol.ASK) {
      reason = `${decision.reason}\n\n${APPROVAL_UNAVAILABLE}`
    } else if (kind === protocol.BLOCK || kind === protocol.UNVERIFIED) {
      reason = decision.reason
    } else {
      reason = `tackbox returned an unrecognized pre decision ${String(kind)}`
    }
    return {
      code: 0,
      stdout: `${JSON.stringify({ decision: 'block', reason })}\n`,
      stderr: '',
    }
  }
  if (kind === protocol.BLOCK) {
    return {
      code: 0,
      stdout: `${JSON.stringify({ decision: 'block', reason: decision.reason })}\n`,
      stderr: '',
    }
  }
  const reason = kind === protocol.UNVERIFIED
    ? decision.reason
    : `tackbox returned an unrecognized post decision ${String(kind)}`
  return {
    code: 0,
    stdout: `${JSON.stringify({
      hookSpecificOutput: {
        hookEventName: POST,
        additionalContext: protocol.unverifiedPostMessage(reason),
      },
    })}\n`,
    stderr: '',
  }
}

async function main() {
  const event = protocol.parseJson(fs.readFileSync(0, 'utf8'))
  if (event.error !== null) {
    process.stderr.write(`tackbox Codex hook: unreadable stdin: ${event.error}\n`)
    process.exitCode = 2
    return
  }
  const result = await handle(event.value)
  if (result.stdout) process.stdout.write(result.stdout)
  if (result.stderr) process.stderr.write(result.stderr)
  process.exitCode = result.code
}

module.exports = {
  APPLY_PATCH,
  APPROVAL_UNAVAILABLE,
  POST,
  PRE,
  handle,
  main,
  render,
  requestFromEvent,
}

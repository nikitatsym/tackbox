// Tackbox as an OMP extension over the strict shared hook protocol.

const payload = require('./payload')
const hook = require('./hook')
const manifest = require('../../package.json')

const CONFIRM_TITLE = 'tackbox approval'
const HEADLESS_NOTE =
  'tackbox cannot ask here (no interactive session), so the call is blocked' +
  ' instead of approved. Re-issue it interactively, or drop the gated line.'
const DENIED = 'tackbox: approval denied.'
const BLOCKED = 'tackbox blocked this change:'

module.exports = function tackbox(pi) {
  pi.setLabel('tackbox')

  pi.on('tool_call', async (event, ctx) => {
    const normalized = payload.normalize(event && event.toolName, event && event.input, ctx && ctx.cwd)
    if (normalized === null) return undefined
    if (normalized.failure) return applyPre(hook.unverified(normalized.failure), ctx)
    if (normalized.unknown === null && normalized.targets.length === 0 && normalized.targetless === 'opaque') {
      return undefined
    }
    const decision = await hook.decide(hook.request('pre', ctx.cwd, normalized), options(ctx))
    return applyPre(decision, ctx)
  })

  pi.on('tool_result', async (event, ctx) => {
    const normalized = payload.normalizeResult(
      event && event.toolName,
      event && event.details,
      event && event.input,
      ctx && ctx.cwd,
      event && event.isError,
    )
    if (normalized === null) return undefined
    const decision = normalized.failure
      ? hook.unverified(normalized.failure)
      : await hook.decide(hook.request('post', ctx.cwd, normalized), options(ctx))
    return applyPost(decision, event)
  })
}

async function applyPre(decision, ctx) {
  if (decision.kind === hook.BLOCK || decision.kind === hook.UNVERIFIED) {
    return { block: true, reason: decision.reason }
  }
  if (decision.kind === hook.ALLOW) return undefined
  if (decision.kind !== hook.ASK) {
    return { block: true, reason: `tackbox: unrecognized pre decision ${String(decision.kind)}` }
  }
  if (!ctx || !ctx.hasUI || !ctx.ui || typeof ctx.ui.confirm !== 'function') {
    return { block: true, reason: `${decision.reason}\n\n${HEADLESS_NOTE}` }
  }
  const approved = await ctx.ui.confirm(CONFIRM_TITLE, decision.reason)
  if (approved === true) return undefined
  return { block: true, reason: `${DENIED}\n${decision.reason}` }
}

function applyPost(decision, event) {
  const kind = decision && decision.kind
  if (kind === hook.BLOCK) {
    return {
      content: appended(event, `${BLOCKED}\n${decision.reason}`),
      isError: true,
    }
  }
  if (kind === hook.UNVERIFIED) {
    return {
      content: appended(event, unverifiedPostMessage(decision.reason)),
    }
  }
  if (kind === hook.ALLOW) return undefined
  return {
    content: appended(
      event,
      unverifiedPostMessage(`tackbox returned an unrecognized post decision ${String(kind)}`),
    ),
  }
}

function unverifiedPostMessage(reason) {
  const text = typeof reason === 'string' ? reason : String(reason)
  if (
    text.includes('The mutation may already have landed.') &&
    text.includes('Do not repeat the mutation; dev.py check remains required.')
  ) {
    return text
  }
  return [
    'The mutation may already have landed.',
    `Tackbox verification did not complete: ${text}`,
    'Do not repeat the mutation; dev.py check remains required.',
  ].join('\n')
}

function appended(event, text) {
  const content = Array.isArray(event && event.content) ? event.content : []
  return [...content, { type: 'text', text }]
}

function options(ctx) {
  return {
    env: process.env,
    version: manifest.version,
    timers: managedTimers(ctx),
  }
}

function managedTimers(ctx) {
  if (!ctx || typeof ctx.setTimeout !== 'function' || typeof ctx.clearTimer !== 'function') {
    return null
  }
  return {
    set: (fn, ms) => ctx.setTimeout(fn, ms),
    clear: handle => ctx.clearTimer(handle),
  }
}

// Browser report helper: empty DSN = log-only no-op.
// Mirrors the gmux/sts-wand pattern — single funnel for toast,
// Sentry capture, optional diagnostic stream.

const Sentry = require('@sentry/browser')

let ready = false
let rateWindow = 60_000
let flushTimeout = 2_000
const lastSent = new Map()

function init(opts = {}) {
  const dsn = opts.dsn || ''
  if (!dsn) {
    if (!opts.silentMissing) {
      console.log('[tackbox] WARN report: DSN unset, capture disabled, running log-only')
    }
    return
  }
  Sentry.init({
    dsn,
    release: opts.release,
    environment: opts.environment,
    debug: !!opts.debug,
    defaultIntegrations: false,
    integrations: opts.integrations || [],
  })
  ready = true
  if (opts.rateWindow > 0) rateWindow = opts.rateWindow
  if (opts.flushTimeout > 0) flushTimeout = opts.flushTimeout
  if (opts.verify) {
    const ok = verify(opts.verifyTimeout || 3_000)
    ok.then(success => {
      if (success) console.log('[tackbox] report: capture verified, DSN=' + maskDSN(dsn))
      else console.warn('[tackbox] report.Init verify: flush timeout, capture endpoint unreachable or rejecting')
    })
    return
  }
  console.log('[tackbox] report: capture enabled (unverified), DSN=' + maskDSN(dsn))
}

function isReady() { return ready }

async function verify(timeout) {
  if (!ready) return false
  Sentry.withScope(scope => {
    scope.setLevel('info')
    scope.setFingerprint(['report.startup'])
    scope.setTag('healthcheck', 'true')
    Sentry.captureMessage('report.Verify')
  })
  return await Sentry.flush(timeout)
}

async function flush(timeout) {
  if (!ready) return
  await Sentry.flush(timeout || flushTimeout)
}

function shouldDrop(key) {
  if (!key) return false
  const now = Date.now()
  const prev = lastSent.get(key)
  if (prev !== undefined && now - prev < rateWindow) return true
  lastSent.set(key, now)
  return false
}

// captureEvent is the gated Sentry sink: nothing before init or inside the
// rate window. Shared by emit (report*) and reportQuiet.
function captureEvent(level, msg, cause, tags, dedupKey) {
  if (!ready || shouldDrop(dedupKey)) return
  const causeErr = cause instanceof Error ? cause : (cause == null ? null : new Error(String(cause)))
  Sentry.withScope(scope => {
    scope.setLevel(level)
    if (dedupKey) scope.setFingerprint([dedupKey])
    if (tags) for (const k of Object.keys(tags)) scope.setTag(k, String(tags[k]))
    Sentry.captureException(causeErr ? new Error(msg, { cause: causeErr }) : new Error(msg))
  })
}

function emit(level, msg, cause, tags, dedupKey) {
  console[level === 'error' ? 'error' : 'warn'](`[${level.toUpperCase()}] ${msg}:`, cause)
  // D005: user lane delivers always, before the init + rate-window gate
  dispatchEventSafely('tackbox:error', { msg, cause, tags, dedupKey, level })
  captureEvent(level, msg, cause, tags, dedupKey)
}

function reportError(msg, cause, tags, dedupKey) {
  emit('error', msg, cause, tags, dedupKey)
}

function reportWarn(msg, cause, tags, dedupKey) {
  emit('warning', msg, cause, tags, dedupKey)
}

function reportSynthError(msg, tags, dedupKey) {
  // synth has no caught error; null keeps the capture a plain Error(msg)
  emit('error', msg, null, tags, dedupKey)
}

// reportQuiet: warning-level capture with no user lane. For background /
// self-healed / degraded-with-fallback failures.
function reportQuiet(msg, cause, tags, dedupKey) {
  console.warn(`[QUIET] ${msg}:`, cause)
  captureEvent('warning', msg, cause, tags, dedupKey)
}

// notify: user lane only, no capture and no rate-window state touched, so a
// following reportError/reportWarn with the same dedupKey still captures. For
// an expected environmental fault (the user lost connectivity). cause is the
// caught error the notice is about.
function notify(msg, cause, tags, dedupKey) {
  console.warn(`[NOTICE] ${msg}:`, cause)
  dispatchEventSafely('tackbox:error', { msg, cause, tags, dedupKey, level: 'notice' })
}

function reportPanic(name, recovered) {
  const key = 'panic:' + name
  console.error(`[FATAL] panic in ${name}:`, recovered)
  dispatchEventSafely('tackbox:error', { msg: 'panic in ' + name, cause: recovered, tags: { source: name }, dedupKey: key, level: 'fatal' })
  if (!ready || shouldDrop(key)) return
  Sentry.withScope(scope => {
    scope.setLevel('fatal')
    scope.setTag('source', name)
    scope.setFingerprint([key])
    Sentry.captureException(recovered instanceof Error ? recovered : new Error(String(recovered)))
  })
}

function setupGlobalHandlers() {
  if (typeof window === 'undefined') return
  window.addEventListener('error', e => {
    reportError('uncaught global error from window', e.error || e.message, { source: 'window.error' }, 'global.uncaught')
  })
  window.addEventListener('unhandledrejection', e => {
    reportError('unhandled promise rejection from window', e.reason, { source: 'window.unhandledrejection' }, 'global.unhandled')
  })
}

function maskDSN(dsn) {
  // no-report: malformed user DSN, opaque marker is the recovery
  try {
    const u = new URL(dsn)
    return u.host + u.pathname
  } catch (e) {
    return '<malformed>'
  }
}

let dispatching = false

function dispatchEventSafely(name, detail) {
  if (typeof window === 'undefined' || typeof CustomEvent === 'undefined') return
  // Re-entrancy guard: a throwing `tackbox:error` listener surfaces via
  // window.onerror (the DOM routes listener failures there, not to dispatchEvent),
  // which setupGlobalHandlers turns back into reportError -> dispatch on the same
  // stack. Skip the nested dispatch so that cannot loop; sequential dispatches
  // are unaffected (D005 deliver-always intact).
  if (dispatching) {
    console.warn('[tackbox] report: nested tackbox:error dispatch skipped (listener-failure re-entry)')
    return
  }
  dispatching = true
  // no-report: dispatch failure loses only the notice; the verb's local log already ran
  try {
    window.dispatchEvent(new CustomEvent(name, { detail }))
  } catch (e) {
    // dispatch failed
  } finally {
    dispatching = false
  }
}

module.exports = {
  init,
  flush,
  isReady,
  verify,
  reportError,
  reportWarn,
  reportQuiet,
  reportSynthError,
  notify,
  reportPanic,
  setupGlobalHandlers,
}

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

function emit(level, msg, cause, tags, dedupKey) {
  console[level === 'error' ? 'error' : 'warn'](`[${level.toUpperCase()}] ${msg}:`, cause)
  // D005: user lane delivers always, before the init + rate-window gate
  dispatchEventSafely('tackbox:error', { msg, cause, tags, dedupKey, level })
  if (!ready || shouldDrop(dedupKey)) return
  const causeErr = cause instanceof Error ? cause : (cause == null ? null : new Error(String(cause)))
  Sentry.withScope(scope => {
    scope.setLevel(level)
    if (dedupKey) scope.setFingerprint([dedupKey])
    if (tags) for (const k of Object.keys(tags)) scope.setTag(k, String(tags[k]))
    Sentry.captureException(causeErr ? new Error(msg, { cause: causeErr }) : new Error(msg))
  })
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

function reportPanic(name, recovered) {
  const key = 'panic:' + name
  // eslint-disable-next-line tackbox/no-console-error
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

function dispatchEventSafely(name, detail) {
  if (typeof window === 'undefined' || typeof CustomEvent === 'undefined') return
  // no-report: event dispatch only, capture already happened upstream
  try {
    window.dispatchEvent(new CustomEvent(name, { detail }))
  } catch (e) {
    // dispatch failed
  }
}

module.exports = {
  init,
  flush,
  isReady,
  verify,
  reportError,
  reportWarn,
  reportSynthError,
  reportPanic,
  setupGlobalHandlers,
}

// Lane router shared by the browser and Node report variants. Each variant binds
// its user-lane sink and its Sentry SDK; every createReporter call owns its own
// state. Contracts: docs/report-contracts.md.

export function createReporter(deliverNotice, sdk) {
  let ready = false
  let rateWindow = 60_000
  let flushTimeout = 2_000
  let dispatching = false
  const lastSent = new Map()

  function init(opts = {}) {
    const dsn = opts.dsn || ''
    if (!dsn) {
      if (!opts.silentMissing) {
        console.log('[tackbox] WARN report: DSN unset, capture disabled, running log-only')
      }
      return
    }
    sdk.init({
      dsn,
      release: opts.release,
      environment: opts.environment,
      debug: !!opts.debug,
      defaultIntegrations: false,
      // Without it the SDK drops Error.cause and the event carries only the Error(msg) wrapper.
      integrations: [sdk.linkedErrorsIntegration(), ...(opts.integrations || [])],
    })
    ready = true
    if (opts.rateWindow > 0) rateWindow = opts.rateWindow
    if (opts.flushTimeout > 0) flushTimeout = opts.flushTimeout
    if (opts.verify) {
      verify(opts.verifyTimeout || 3_000).then(success => {
        if (success) console.log('[tackbox] report: capture verified, DSN=' + maskDSN(dsn))
        else console.warn('[tackbox] report.Init verify: flush timeout, capture endpoint unreachable or rejecting')
      })
      return
    }
    console.log('[tackbox] report: capture enabled (unverified), DSN=' + maskDSN(dsn))
  }

  function isReady() { return ready }

  async function verify(timeout = 3_000) {
    if (!ready) return false
    sdk.withScope(scope => {
      scope.setLevel('info')
      scope.setFingerprint(['report.startup'])
      scope.setTag('healthcheck', 'true')
      sdk.captureMessage('report.Verify')
    })
    return await sdk.flush(timeout)
  }

  async function flush(timeout) {
    if (!ready) return
    await sdk.flush(timeout || flushTimeout)
  }

  function shouldDrop(key) {
    if (!key) return false
    const now = Date.now()
    const prev = lastSent.get(key)
    if (prev !== undefined && now - prev < rateWindow) return true
    lastSent.set(key, now)
    return false
  }

  // The gated Sentry sink: nothing before init or inside the rate window.
  function captureEvent(level, msg, cause, tags, dedupKey) {
    if (!ready || shouldDrop(dedupKey)) return
    const causeErr = cause instanceof Error ? cause : (cause == null ? null : new Error(String(cause)))
    sdk.withScope(scope => {
      scope.setLevel(level)
      if (dedupKey) scope.setFingerprint([dedupKey])
      if (tags) for (const k of Object.keys(tags)) scope.setTag(k, String(tags[k]))
      sdk.captureException(causeErr ? new Error(msg, { cause: causeErr }) : new Error(msg))
    })
  }

  // A sink failure is captured on the quiet lane, never thrown into the caller.
  // A sink that re-enters a verb on the same stack is skipped so it cannot loop;
  // sequential dispatches are unaffected (D005 deliver-always intact).
  function dispatch(notice) {
    if (dispatching) {
      console.warn('[tackbox] report: nested user-lane dispatch skipped (re-entry from the user-lane sink)')
      return
    }
    dispatching = true
    try {
      const pending = deliverNotice(notice)
      if (pending && typeof pending.then === 'function') {
        pending.then(undefined, err => reportQuiet('report notifier failed', err, null, 'report.notifier'))
      }
    } catch (err) {
      reportQuiet('report notifier failed', err, null, 'report.notifier')
    } finally {
      dispatching = false
    }
  }

  function emit(level, msg, cause, tags, dedupKey) {
    console[level === 'error' ? 'error' : 'warn'](`[${level.toUpperCase()}] ${msg}:`, cause)
    // D005: user lane delivers always, before the init + rate-window gate
    dispatch({ msg, cause, tags, dedupKey, level })
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

  function reportQuiet(msg, cause, tags, dedupKey) {
    console.warn(`[QUIET] ${msg}:`, cause)
    captureEvent('warning', msg, cause, tags, dedupKey)
  }

  function notify(msg, cause, tags, dedupKey) {
    console.warn(`[NOTICE] ${msg}:`, cause)
    dispatch({ msg, cause, tags, dedupKey, level: 'notice' })
  }

  function reportPanic(name, recovered) {
    const key = 'panic:' + name
    console.error(`[FATAL] panic in ${name}:`, recovered)
    dispatch({ msg: 'panic in ' + name, cause: recovered, tags: { source: name }, dedupKey: key, level: 'fatal' })
    if (!ready || shouldDrop(key)) return
    sdk.withScope(scope => {
      scope.setLevel('fatal')
      scope.setTag('source', name)
      scope.setFingerprint([key])
      sdk.captureException(recovered instanceof Error ? recovered : new Error(String(recovered)))
    })
  }

  return {
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
  }
}

const DSN_RE = /^[a-z][a-z0-9+.-]*:\/\/(?:[^@/?#]*@)?([^/?#]+)([^?#]*)/i

// Host and path for logs; the public key in the userinfo stays out.
function maskDSN(dsn) {
  const m = DSN_RE.exec(dsn)
  return m ? m[1] + m[2] : '<malformed>'
}

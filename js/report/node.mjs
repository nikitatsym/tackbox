// Node report helper on @sentry/node. Node has no window event, so the user lane
// is a registered notifier, as in the Go, Python, and Java helpers.

import * as Sentry from '@sentry/node'
import { createReporter } from './core.mjs'

let notifier = null

export function setNotifier(fn) {
  if (fn !== null && typeof fn !== 'function') {
    throw new TypeError('setNotifier: expected a function or null, got ' + typeof fn)
  }
  notifier = fn
}

function callNotifier(notice) {
  return notifier === null ? undefined : notifier(notice)
}

export const {
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
} = createReporter(callNotifier, {
  init: Sentry.init,
  flush: Sentry.flush,
  withScope: Sentry.withScope,
  captureException: Sentry.captureException,
  captureMessage: Sentry.captureMessage,
  linkedErrorsIntegration: Sentry.linkedErrorsIntegration,
})

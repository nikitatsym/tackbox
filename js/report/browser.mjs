// Browser report helper on @sentry/browser. The user lane is the
// `tackbox:error` CustomEvent dispatched on window.

import * as Sentry from '@sentry/browser'
import { createReporter } from './core.mjs'

function dispatchNotice(notice) {
  // SSR or a worker: there is no window to carry the user lane.
  if (typeof window === 'undefined' || typeof CustomEvent === 'undefined') return
  window.dispatchEvent(new CustomEvent('tackbox:error', { detail: notice }))
}

// SDK functions are picked statically so a bundler can tree-shake the rest of @sentry/browser.
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
} = createReporter(dispatchNotice, {
  init: Sentry.init,
  flush: Sentry.flush,
  withScope: Sentry.withScope,
  captureException: Sentry.captureException,
  captureMessage: Sentry.captureMessage,
  linkedErrorsIntegration: Sentry.linkedErrorsIntegration,
})

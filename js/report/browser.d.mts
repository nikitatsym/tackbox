import type { BrowserOptions } from '@sentry/browser'
import type { BaseInitOptions, Notice } from './api.mjs'

export type { Notice, NoticeLevel, Tags } from './api.mjs'
export {
  isReady,
  verify,
  flush,
  reportError,
  reportWarn,
  reportQuiet,
  reportSynthError,
  notify,
  reportPanic,
} from './api.mjs'

type Integration = Extract<NonNullable<BrowserOptions['integrations']>, readonly unknown[]>[number]

export interface InitOptions extends BaseInitOptions {
  /** Added to the helper's own integration set; the SDK defaults stay off. */
  integrations?: readonly Integration[] | undefined
}

/** Start capture. An empty DSN keeps the helper log-only. */
export declare function init(opts?: InitOptions): void

declare global {
  interface WindowEventMap {
    /** The user lane: dispatched on window by every verb except `reportQuiet`. */
    'tackbox:error': CustomEvent<Notice>
  }
}

import type { NodeOptions } from '@sentry/node'
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

type Integration = Extract<NonNullable<NodeOptions['integrations']>, readonly unknown[]>[number]

export interface InitOptions extends BaseInitOptions {
  /** Added to the helper's own integration set; the SDK defaults stay off. */
  integrations?: readonly Integration[] | undefined
}

/** Start capture. An empty DSN keeps the helper log-only. */
export declare function init(opts?: InitOptions): void

/**
 * The user-lane sink. It runs synchronously on the reporting caller's stack,
 * before the capture gate. A throw or a rejected returned promise is captured
 * on the quiet lane (`report notifier failed`, dedupKey `report.notifier`) and
 * never reaches the caller.
 */
export type Notifier = (notice: Notice) => void | Promise<void>

/** Register the user-lane sink; `null` clears it. With none registered the user lane is a no-op. */
export declare function setNotifier(notifier: Notifier | null): void

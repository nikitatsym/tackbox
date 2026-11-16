// Verbs and types shared by tackbox/report/browser and tackbox/report/node.
// Lane routing, rate window, panic keys, and scope isolation:
// https://github.com/nikitatsym/tackbox/blob/main/docs/report-contracts.md

/** Sentry tags; values are sent as strings. */
export type Tags = Readonly<Record<string, string>>

/** `notice` is the user-lane-only level `notify` carries. */
export type NoticeLevel = 'error' | 'warning' | 'notice' | 'fatal'

/**
 * One user-lane event. The helper never suppresses the user lane; the app owns
 * rendering and any coalescing, keyed on `dedupKey`.
 */
export interface Notice {
  readonly msg: string
  /** The caught error; `null` for `reportSynthError`, the recovered value for `reportPanic`. */
  readonly cause: unknown
  readonly tags: Tags | null
  /** The verb's dedupKey; `panic:<name>` for `reportPanic`. */
  readonly dedupKey: string
  readonly level: NoticeLevel
}

export interface BaseInitOptions {
  /** Empty or absent: no capture, log-only; the user lane still delivers. */
  dsn?: string | undefined
  release?: string | undefined
  environment?: string | undefined
  debug?: boolean | undefined
  /** Skip the WARN line an empty DSN logs. */
  silentMissing?: boolean | undefined
  /** Per-dedupKey capture rate window in ms; default 60000. */
  rateWindow?: number | undefined
  /** Default `flush` timeout in ms; default 2000. */
  flushTimeout?: number | undefined
  /** Send one `report.startup` healthcheck event and flush it in the background. */
  verify?: boolean | undefined
  /** Healthcheck flush timeout in ms; default 3000. */
  verifyTimeout?: number | undefined
}

export declare function isReady(): boolean

/**
 * Send one healthcheck event (fingerprint `report.startup`) and flush.
 * Resolves false before a successful `init` or when the flush times out.
 */
export declare function verify(timeout?: number): Promise<boolean>

/** Drain queued events; a no-op before a successful `init`. */
export declare function flush(timeout?: number): Promise<void>

/** Local log, user lane, error-level capture. */
export declare function reportError(msg: string, cause: unknown, tags: Tags | null, dedupKey: string): void

/** Local log, user lane, warning-level capture. */
export declare function reportWarn(msg: string, cause: unknown, tags: Tags | null, dedupKey: string): void

/** Local log and warning-level capture, no user lane: background, self-healed, or degraded-with-fallback failures. */
export declare function reportQuiet(msg: string, cause: unknown, tags: Tags | null, dedupKey: string): void

/** `reportError` without a caught error: the capture is a plain `Error(msg)`. */
export declare function reportSynthError(msg: string, tags: Tags | null, dedupKey: string): void

/**
 * Local log and user lane only: no capture and no rate-window slot, so a later
 * `reportError` / `reportWarn` with the same dedupKey still captures. For an
 * expected environmental fault such as lost connectivity.
 */
export declare function notify(msg: string, cause: unknown, tags: Tags | null, dedupKey: string): void

/** Fatal-level capture fingerprinted and rate-limited per name as `panic:<name>`, plus the user lane. */
export declare function reportPanic(name: string, recovered: unknown): void

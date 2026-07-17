# report helper contracts

Cross-language contracts of the runtime capture helpers (go/report,
js/report.js, java/report, py/tackbox_report). Entries keep their
original rules/DECISIONS.md ids - code comments, DESIGN docs, and the
specs reference them as D002/D003/D005. Rule decisions stay in
rules/DECISIONS.md; this file holds library behavior.

## Direct reporting lanes

Language-specific helper names map to these semantic verbs.
`reportSynthError` follows the `error` row; `crumb` applies where a package
exposes it.

| semantic verb | local log | user lane | telemetry |
| --- | --- | --- | --- |
| `error` | error | error notice | error event |
| `warn` | warning | warning notice | warning event |
| `quiet` | warning | none | warning event |
| `notify` | warning | notice | none |
| `panic` | fatal-equivalent | fatal notice | fatal event |
| `crumb` | none | none | breadcrumb, not an event |

D005 below owns sink ordering and rate-limit behavior; this table only maps
direct verbs to lanes. Breadcrumbs are readiness-gated, do not consume D005
rate-limit state, and are not reporting events.

## D002 - per-name fingerprints for background tasks and panics

The background-task and panic primitives fingerprint and rate-limit
per task name, not per class: the task error path keys
`go.task:<name>` (Go) / `task:<name>` (Java), panic keys
`panic:<name>`. Two differently named tasks failing inside one rate
window each surface as their own issue instead of collapsing.

Why: a background failure needs individual telemetry visibility; a
shared constant key means one fingerprint and one rate bucket for
every task, so the second failure inside the window is silently
dropped.

Boundary with the literal-dedupKey rules (ERC006 arm 3 and analogs):
those target application call sites, where a computed key means
unbounded cardinality from untrusted input. The blessed helper builds
these keys through its package-internal capture core by design - it
owns a small closed set of task names - not via an escape marker.

## D003 - concurrency-isolated direct capture

Each direct event capture owns an isolated event scope. The guarantee covers
error, warning, quiet, panic, and verify captures in every helper that exposes
those operations.

Concurrent reporter calls must not bleed fingerprints or tags between events.
Isolation preserves grouping and event context under application concurrency.

Named gap: breadcrumbs use process-global SDK state and remain outside the
event-scope guarantee. The rate-limit maps are memory-safe under concurrency;
the Go and Java check-then-set operations may admit one extra in-window
capture during a race.

## D005 - dedup rate-limits telemetry, never the user lane

Deduplication lives at two levels with different owners:

- Telemetry: the helper rate-limits captures per dedupKey (default
  60s window) - a repeat with the same dedupKey inside the window is
  dropped client-side, so the server never sees it. Lossy for in-window
  repeats (their occurrence count and any changed context are lost);
  only captures that pass the window reach the server, which groups
  by fingerprint.
- The user lane is never suppressed by the helper. Every user-facing
  event is delivered, carrying its dedupKey; collapsing a storm into
  one live banner, a counter, or a per-click toast is presentation
  policy and belongs to the app's listener, keyed on that dedupKey.

Why the asymmetry: dropping an in-window telemetry repeat loses only
duplicate detail (occurrence count and any changed context) - the
failure itself is already recorded from the first capture in the
window; suppressing the user lane loses the failure for its only
audience - a notification dropped inside the helper is a swallowed
error at the UI level, the exact failure mode tackbox exists to
prevent. How to collapse is per-app UX policy; a library-imposed one
would get worked around.

Per-sink order at a call site: local log always; user-lane dispatch
unconditional, before any gate; capture behind init + rate window. A
helper without a user lane simply has no second sink; the contract
binds it as soon as one ships.

# report helper contracts

Cross-language contracts of the runtime capture helpers (go/report,
js/report.js, java/report, py/tackbox_report). Entries keep their
original rules/DECISIONS.md ids - code comments, DESIGN docs, and the
specs reference them as D002/D003/D005. Rule decisions stay in
rules/DECISIONS.md; this file holds library behavior.

## D002 - per-name fingerprints for background tasks and panics

The background-task and panic primitives fingerprint and rate-limit
per task name, not per class: the task error path keys
`go.task:<name>` (Go) / `task:<name>` (Python, Java), panic keys
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

## D003 - concurrency-isolated capture

Every event-capture site owns an isolated scope: go/report clones the
current hub per capture (`sentry.CurrentHub().Clone()` +
`hub.WithScope`); the Python and Java helpers hold the same guarantee
through their SDKs' isolation idioms (see each helper's DESIGN.md).
Covers the error/warn core, panic, and the Verify healthcheck.

Why: background tasks capture concurrently on a process-wide hub, and
a shared scope stack bleeds fingerprint/tags between concurrent
captures - memory-safe but grouping-corrupt. Isolation is what makes
D002's per-name fingerprints hold under real concurrency.

Named gap: breadcrumbs still write to the global hub; the packages
are not request-scoped, so breadcrumb isolation stays out of scope.
The rate limit is memory-safe under concurrency (independently of
scope isolation); the check-then-set is not atomic, so a rare race may
let one extra in-window repeat ship - benign for a rate limit.

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

Why, the asymmetry: suppressing telemetry loses nothing (the server
counts); suppressing the user lane loses the failure for its only
audience - a notification dropped inside the helper is a swallowed
error at the UI level, the exact failure mode tackbox exists to
prevent. How to collapse is per-app UX policy; a library-imposed one
would get worked around.

Per-sink order at a call site: local log always; user-lane dispatch
unconditional, before any gate; capture behind init + rate window. A
helper without a user lane simply has no second sink; the contract
binds it as soon as one ships.

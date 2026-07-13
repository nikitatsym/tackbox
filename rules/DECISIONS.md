# tackbox rule decisions

Canonical record of load-bearing rule decisions. One entry per
decision. A commit that changes rule behavior must add or amend an
entry here. This file is the authority: the specs state the intent,
the per-language READMEs render it for users, git messages are not a
decision home.

## Scope

tackbox enforces a minimal, structural discipline for how code handles
failure and keeps tests honest: properties decidable from the AST,
uniform in intent across languages. It does not analyze values or data
content.

Inclusion test for a candidate rule:

- About the SHAPE of error handling or test integrity -> in.
  About the CONTENT of a value (is this data a secret, is it PII, is
  the business logic correct) -> out.
- Decidable from the AST without value or taint analysis -> in.
  Needs to know what a value contains -> out.

Out of scope, by design: secret detection, PII scanning, business
correctness. Those are a dedicated tool's job (a secret scanner on
values, a type checker, business tests), not tackbox's.

## D001 - secret-name fingerprint removed (2026-07-13)

Rules affected: ERC006 secret-arm (Go), JV008 (Java),
no-secret-in-report (JS), TBX009 (Python).

Decision: remove the identifier-name secret heuristic entirely. It
flagged a reporter argument whose identifier name contained a
substring from {token, password, key, secret, cookie}.

Rationale: out of scope (see Scope) - it inspects value content, not
error-handling shape, and it is undecidable from a name. It was also
the sole source of cross-engine divergence (four separate, unequal
implementations; only Go could exclude type names, and only via type
info) and of false-positive churn (`tokens`, `publicKey`, `TokenKind`
all matched by substring). No proven real catch across the consumer
history; real secret-leak prevention was done by header/body scrubbing
and review.

Kept - the other two arms of ERC006, structural, not name-based:

- user-input arm: a reporter argument may not carry raw `*http.Request`
  input (`r.URL.Path`, `r.Body`, `r.Header.Get`). Go only, where it is
  type-precise; its concern is telemetry-grouping hygiene (unbounded
  cardinality) plus incidental privacy. Other languages get grouping
  hygiene from the literal-dedupKey rule instead.
- dedupKey arm: the tier-1 capture call must carry a well-formed
  string-literal dedupKey (`area.suffix[:id]`).

Privacy still holds as a principle (no secret values in
message/tags/dedupKey) but is enforced by scrubbing and review, not by
a name lint. If value-level secret scanning is ever wanted, delegate it
to a dedicated scanner (gitleaks / trufflehog) on a separate track.

## D002 - per-name fingerprints for GoSafe and Panic (2026-07-13)

Rules affected: none. This records a go/report library contract and
its boundary with ERC006 (dedupKey arm); no lint rule changes.

Decision: the go/report background-task and panic primitives
fingerprint and rate-limit PER GOROUTINE NAME, not per class. GoSafe's
error path keys on `go.task:<name>`; Panic keys on `panic:<name>`.
Both keys are built directly from the name, so two differently-named
background tasks (or panics) failing inside the 60s rate window each
surface as their own Glitchtip issue instead of collapsing into one.

Rationale: a background failure needs individual Glitchtip visibility.
The previous error path passed the constant literal `go.task` to the
public SentryErr, so every goroutine's error shared one fingerprint
and one rate-limit bucket; a second task failing within the window was
silently dropped and all failures grouped as a single issue. The name
was only a tag. Panic was already per-name, so this also restores
consistency between GoSafe's two paths (panic and error). An earlier
goSafe keyed `goroutine:<name>`; `go.task:<name>` is the current form.

A library primitive legitimately builds its fingerprint directly. The
static-literal dedupKey rule (ERC006 arm 3) targets application call
sites, where a computed key would mean unbounded telemetry cardinality
from untrusted input. It does not target the blessed go/report wrapper,
which owns a small closed set of goroutine names. GoSafe and Panic
therefore set the fingerprint through the package-internal capture
core, bypassing the public SentryErr literal-key contract by design -
not by an escape marker.

## D003 - concurrency-isolated capture in go/report (2026-07-13)

Rules affected: none. This records a go/report library contract; no
lint rule changes.

Decision: every event-capture site in go/report clones the current hub
before setting scope and shipping the event
(`sentry.CurrentHub().Clone()`, then `hub.WithScope` + `hub.Capture*`
on the clone). This covers the error/warn core (capture), Panic, and
the Verify healthcheck. Each capture therefore owns an isolated scope
instead of pushing onto sentry-go's shared global scope stack.

Rationale: GoSafe runs tasks in goroutines by design, so captures run
concurrently on the process-wide hub. sentry-go's global scope stack
is shared, so concurrent WithScope/Capture calls on it can bleed scope
between goroutines - an event can pick up another goroutine's
fingerprint and tags. It is memory-safe (no data race) but logically
corrupts grouping under simultaneity. Cloning is the documented
sentry-go per-goroutine idiom and gives each capture its own scope.

This completes D002: the per-name fingerprints (`go.task:<name>`,
`panic:<name>`) only hold under real concurrency because of this
isolation; without it, two background tasks failing at the same instant
could swap fingerprints and mis-group. Cloning preserves global
context - Release/Environment come from the Init ClientOptions applied
by the client, and Clone copies the client plus the top-most scope, so
no Init-time context is dropped and the shared transport that Flush
drains is unchanged.

Rate-limit is unaffected: shouldDrop was already concurrency-safe, keyed
on the string via sync.Map before capture, and stays as-is.

Known limitation: breadcrumbs (Crumb / AddBreadcrumb) still write to the
global hub. The package is not request-scoped, so breadcrumb isolation
is a separate, deeper design concern and is left out of scope here.

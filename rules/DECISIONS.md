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

## D004 - runtime helper capture APIs are tier-1 reporters (2026-07-13)

Rules affected: TBX001 (python-swallowed-exception), JV001 (java
swallow), and the java rules that share the same capture recognition -
JV005 (exit) and JV006 (double capture).

Decision: the runtime capture helpers' PUBLIC capture APIs are
recognized as tier-1 reporters, per language, so a consumer's catch
that hands the caught error to one is credited with no `# no-report:` /
`// no-report:` marker and no `.tackbox-reporters` entry. The helpers
are the blessed reporting path, so adopting them costs zero linter
ceremony.

- Go: `go/report` by import origin (already shipped).
- JS: `tackbox/report` by import origin (already shipped).
- Python: `tackbox_report`'s `report_error` / `report_warn` /
  `report_quiet` / `report_panic` by function NAME - a built-in set in
  pyrules.
  pyrules has no cross-module type info, so recognition is name-based,
  not origin-based. Inherent limitation: a same-named function from
  ANY module is credited too - the engine cannot prove the origin
  source-only. Argument-flow is still required (the caught must reach
  the call), the same gate as the tier-2 declared-reporter path.
- Java: the `nl.tsym.tackbox.report.Report` methods `error` / `warn` /
  `quiet` / `panic` by origin (package + class), through the same source-only
  origin machinery javalint already uses for slf4j, System.Logger, and
  tier-2 declared reporters. A same-named Report from another package,
  or one declared in the consumer's own file, resolves to a different
  origin and is not credited. Report's methods are static, so unlike
  slf4j's instance-only error/warn a fully-qualified static call is a
  real capture too; tier1-eligibility is not required.

Recognition only LOOSENS - it credits more capture sites - so it
cannot introduce a swallow finding on existing code. JV006 does count
a recognized Report capture as a capture (report + rethrow is a double
capture), matching how it already counts slf4j and declared reporters.

This also retires the two `# no-report:` markers the Python helper's
background-task wrappers (`run_task` / `run_task_async`) carried: each
internal `except` now calls `report_error` directly - identical
routing (per-name `task:<name>` fingerprint, log-before-drop,
rate-limit) - so the background boundary is a recognized capture, not
a false-positive swallow.

Amendment (2026-07-15): the `quiet` verb (Go `Quiet`, JS `reportQuiet`,
Python `report_quiet`, Java `quiet`) is a capture - it skips only the
user lane - so it joins each language's capture set above, including
the dedupKey-shape validation where the engine has it. `notify` is NOT
a capture and is never credited by these sets; whether notify
terminates an err-branch is a separate, gated decision.

Scope unchanged (D001): this is capture-shape recognition, decidable
from the AST and imports, not value or content analysis. See the
tackbox runtime-helpers plan (Python + Java capture helpers) for the
full helper design.

## D005 - dedup rate-limits telemetry, never the user lane (2026-07-14)

Rules affected: none. This records a cross-language runtime-helper
library contract (like D002/D003); no lint rule changes.

Decision: deduplication lives at two levels with different owners.

- Telemetry: the helper rate-limits captures per dedupKey (default 60s
  window) before they ship. Suppressing here is lossless - the server
  already groups by fingerprint and counts repeats, so a dropped
  duplicate changes nothing about visibility.
- The user lane is never suppressed by the helper. Every user-facing
  event is delivered, each carrying its dedupKey; collapsing a storm
  (twelve identical "offline" toasts from a 5s poll loop) into one
  live banner, a counter, or a per-click toast is presentation policy
  and belongs to the app's listener, keyed on the dedupKey the helper
  provides.

Rationale: a notification silently dropped inside the helper is a
swallowed error at the UI level - the user clicks retry, nothing
happens, no toast - the exact failure mode tackbox exists to prevent.
How to collapse is UX policy that differs per app (banner vs counter
vs toast-per-action), so a library-imposed policy would just get
worked around. And a connectivity loss is a STATE, not an event
stream: the right UX is one keyed, live banner - the stable key is
precisely what the helper contributes.

The asymmetry, named: suppressing telemetry loses nothing (the server
counts); suppressing the user lane loses the failure for its only
audience. Hence rate-limit on capture, deliver-always on the user
lane.

Per-sink order at a call site: local log always; user-lane dispatch
unconditional, before any gate; capture behind init + rate window. A
helper without a user lane simply has no second sink; the contract
binds it as soon as one ships.

## D006 - notify is a gated err-branch terminal (2026-07-15)

Rules affected: the swallow rules of every engine (ERC001, JV001,
TBX001, no-swallow-catch) gain a notify arm; new per-engine gate
rules; the double-capture family gains a double-lane arm.

Decision: a `notify` call (user lane only, no capture) is a valid
terminal for a failure path under three structural gates:

- argument-flow: the caught error must reach notify's arguments (the
  same machinery that credits a capture);
- narrowing: the notify path must be provably narrower than the
  failure branch itself. Java/Python: a narrow catch type - not
  Exception / RuntimeException / Throwable / Error / BaseException /
  bare. Go/JS (no typed catch): notify must sit under an additional
  condition inside the err-branch/catch; the complement path is still
  checked by the existing swallow rules.
- exclusivity (double-lane): notify plus any capture verb on the same
  path is a finding - error/warn already reach the user lane, so the
  pair double-shows the user.

An unconditional notify-only in a broad catch is a finding: it would
let everything route to toasts and blind the telemetry. Rationale:
gate strength is proportional to observability loss - notify drops
the only channel the operator sees, hence the hard gate; quiet keeps
telemetry and needs none (D004 amendment).

notify is never credited as a capture. It IS validated like one:
static literal msg (D007) and well-formed literal dedupKey (D008).

## D007 - user-lane msg must be a static literal (2026-07-15)

Rules affected: valid-error-report (JS, already enforced); new arms
in erclint, javalint, pyrules.

Decision: the msg argument of the user-lane verbs - error / warn /
notify - must be a static string literal in every language. msg is
what the user sees and what titles the issue; dynamic data belongs in
cause and tags. The length bounds (15-200) stay JS-only, where the
toast UX defined them. quiet and panic are exempt: quiet is
telemetry-only, panic takes a name, not a msg.

## D008 - literal dedupKey validation goes cross-language (2026-07-15)

Rules affected: ERC006 arm 3 (Go, already enforced), valid-dedup-key
(JS, already enforced); new rules in javalint and pyrules.

Decision: every tier-1 verb call - error / warn / quiet / notify -
must carry a static string-literal dedupKey of the form
`area.suffix[:identifier]`, in all four engines. The key is the
Sentry fingerprint, the rate-limit bucket, and the user-lane
coalescing key (D005) - dedup stands on it in every lane, so its
validation cannot stay a Go/JS privilege. Python validates on the
recognized names (the D004 name-model caveat applies).

Amendment (2026-07-15): the notify gate (D006) and the argument
contracts (D007/D008) exempt test files in every engine - Go
`_test.go`, Java `src/test/`, JS `*.test.* / *.spec.* / __tests__ /
tests/`, Python `test_* / *_test.py / tests/ / conftest.py` - tests
legitimately use dynamic keys (e.g. per-worker keys in concurrency
tests). The test-skip rules are unaffected and keep running in tests
everywhere.

## D009 - suppression-marker reasons have a minimum length (2026-07-15)

Rules affected: the marker parsers of every engine (no-report /
parse-skip / nil-return / test-skip / dup-ok markers).

Decision: a suppression marker's reason must be at least 10
characters after trimming. Non-empty was too cheap: `ok` / `todo`
passed as reasons. No keyword bans - a stop-word list is a content
heuristic with false positives (a todo-list app: the D001 lesson);
length is a structural nudge, and the substance is judged by review
and the approval gate. In-call skip reasons (`t.Skip("...")`,
`{ skip: '...' }`) keep the existing non-empty rule: they are visible
strings in the test body, not lint escapes.

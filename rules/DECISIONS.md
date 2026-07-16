# tackbox rule decisions

Canonical record of load-bearing rule decisions. One entry per
decision. A commit that changes rule behavior must add or amend an
entry here. This file is the authority: the specs state the intent,
the per-language READMEs render it for users, git messages are not a
decision home.

Entry format: Rules affected / Decision (present tense) / short
rationale / named gaps. No history, no examples, no plan or session
references. Amending an entry rewrites it to the present state and
bumps the date in its heading - git keeps the old text; appended
amendment paragraphs are not a form. Runtime-helper library contracts
live in docs/report-contracts.md (D002/D003/D005 moved there, ids
kept).

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

## Design principles

Every mechanism (rule, gate, helper contract, tooling) is judged
against these; a decision that trades one away must name the trade.

- The paved road is self-service; stepping off it costs human
  attention. Adopting a blessed verb needs no ceremony (D004, D010);
  a marker, a tier-2 declaration, or a lane opt-out draws a reason
  and an approval question (D009, D011).
- The paved road is cheaper than the bypass. If evading a rule is
  less work than following it, the rule is broken - fix the road, not
  the fine.
- The bypass surface is enumerable. Everything off the road is
  greppable and lands in one inventory command (D013); a bypass that
  cannot be listed does not exist as a sanctioned mechanism.
- Gate strength is proportional to observability loss. notify drops
  telemetry, so it is hard-gated (D006); quiet keeps telemetry and
  needs no gate (D004 amendment); a marker no engine reads is dead
  and draws no question (D012).
- Inclusion test for a new mechanism: it must not make the bypass
  cheaper. A config flag, a bulk suppression, an unrecorded approval
  cache all lower the cost of stepping off the road - out, unless the
  lost attention or observability is priced back in elsewhere.

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
a name lint.

## D002 - per-name fingerprints for GoSafe and Panic (2026-07-13)

Library contract, moved to docs/report-contracts.md (id kept). Not a
lint rule; its boundary with ERC006 arm 3 is recorded there.

## D003 - concurrency-isolated capture in go/report (2026-07-13)

Library contract, moved to docs/report-contracts.md (id kept).

## D004 - runtime helper capture APIs are tier-1 reporters (2026-07-15)

Rules affected: TBX001 (python swallow), JV001 (java swallow), JV005
(exit), JV006 (double capture), and every rule sharing capture
recognition.

Decision: the runtime capture helpers' public capture APIs are
recognized as tier-1 reporters: a consumer's catch that hands the
caught error to one is credited with no `no-report` marker and no
`.tackbox-reporters` entry - adopting the blessed path costs zero
lint ceremony. The capture set is error / warn / quiet / panic in
each language's naming; quiet skips only the user lane, so it is a
capture and carries the dedupKey-shape validation where the engine
has it. notify is never a capture - whether notify terminates a
failure path is D006's separately gated decision.

Recognition per language:

- Go: `go/report` by import origin.
- JS: `tackbox/report` by import origin.
- Python: `tackbox_report` by file-local import origin; mechanics and
  kill semantics in D010.
- Java: `nl.tsym.tackbox.report.Report` by source-only origin
  (package + class), the same machinery javalint uses for slf4j,
  System.Logger, and tier-2 declared reporters. A same-named Report
  from another package or the consumer's own file resolves to a
  different origin and is not credited. Report's methods are static,
  so a fully-qualified static call is a real capture;
  tier1-eligibility is not required.

Argument-flow is required everywhere: the caught error must reach the
call. Recognition only loosens - it credits more capture sites - so
it cannot introduce a swallow finding. JV006 counts a recognized
Report capture as a capture (report + rethrow is a double capture),
as it counts slf4j and declared reporters. The helpers' own
background-task wrappers route their internal except through the
capture core, so that boundary is a recognized capture and carries no
markers.

Scope unchanged (D001): capture-shape recognition, decidable from the
AST and imports, not value or content analysis.

## D005 - dedup rate-limits telemetry, never the user lane (2026-07-14)

Library contract, moved to docs/report-contracts.md (id kept): the
telemetry rate window, the never-suppressed user lane, and the
per-sink order live there.

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

Rules affected: ERC006 arm 3 (Go), valid-dedup-key (JS), TBX011
(Python), JV010 (Java).

Decision: every tier-1 verb call - error / warn / quiet / notify -
must carry a static string-literal dedupKey of the form
`area.suffix[:identifier]`, in all four engines. The key is the
Sentry fingerprint, the rate-limit bucket, and the user-lane
coalescing key (D005) - dedup stands on it in every lane, so its
validation cannot stay a Go/JS privilege. Python validates on
origin-resolved calls (D010).

The notify gate (D006) and the argument contracts (D007/D008) exempt
test files in every engine - Go `_test.go`, Java `src/test/`, JS
`*.test.* / *.spec.* / __tests__ / tests/`, Python `test_* /
*_test.py / tests/ / conftest.py` - tests legitimately use dynamic
keys (per-worker keys in concurrency tests). The test-skip rules keep
running in tests everywhere.

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

## D010 - Python tier-1 recognition by import origin (2026-07-15)

Rules affected: TBX001 (swallow credit), TBX010 (notify gate,
double-lane), TBX011 (reporter args) - every pyrules site that
recognized tier-1 verbs by bare name. Tier-2 declared reporters are
unchanged.

Decision: pyrules recognizes the tier-1 verbs (report_error /
report_warn / report_quiet / report_panic) and notify by file-local
import origin, not by name. A call counts only when it resolves,
through the module's own import bindings, to the tackbox_report
package:

- `from tackbox_report import report_error [as x]` binds the local
  name; `import tackbox_report [as tr]` binds the module, and
  attribute calls through it (`tr.report_error(...)`) resolve - calls
  a bare-name model cannot credit.
- Kill semantics (ruff's ordered-binding model): a later module-level
  rebinding of the bound name (def / class / assignment / any binding
  statement) or `del` kills the binding from that point; a function
  parameter or local binding shadows it inside that scope; a call
  before the import gets no credit. One source-order pass, position
  sensitive. A def in a try/except ImportError fallback kills too -
  the conservative direction: over-flag, never hide.
- `from tackbox_report import *` binds the five verbs (the exporter
  is our own fixed package, so the star is enumerable); a star import
  from any other module binds nothing and kills nothing - a clobber
  via a star re-export is an accepted, greppable residual.
- The resolver core is vendored from bandit (Apache-2.0, notice
  retained): the import alias map plus dotted-attribute resolution.
  The kill layer is ours, specified by ruff's binding kinds - no
  third-party per-file resolver ships the rebinding-kill semantics
  this rule stands on.

Consequences:

- No reserved names. A consumer's own module-level `def notify` (or
  report_error) is just a function: never credited, not gated by
  TBX010, not validated by TBX011. The de-facto call-site reservation
  D006-D008 introduced for bare `notify(...)` disappears.
- The shadow attack self-defeats: a local def named like a verb kills
  the binding, calls stop being credited, and the swallow rules fire
  on the silent catch. No def-site rule needed.
- TBX011's local-defs exemption is obsolete (origin resolution makes
  it precise) and is removed.

The tackbox_report package itself (a tackbox_report path segment)
self-credits: its own top-level defs of the verbs are the origin, so
internal routing keeps its swallow credit (D004's marker retirement
stands), while TBX010/TBX011 do not bind the owner - the library
builds per-name keys by design (D002). Consumer repos never lint the
installed package, so the segment rule is inert outside this repo.

Tier-2 stays name-based (declared name + argument flow + dead-symbol
validation): origin for tier-2 would need module-path-to-file
resolution - Python's genuinely messy half (src layouts, namespace
packages, relative imports) - for names the consumer already declares
consciously in a user-gated file. Accepted residual gap: a same-named
def in another file still shadows a tier-2 name; the D004 caveat now
applies to tier-2 only.

Facades: a consumer module that re-exports helper verbs
(`from tackbox_report import report_error` in app/reporting.py)
breaks file-local tier-1 origin for its importers; the fix is
declaring the facade in `.tackbox-reporters`. Tier-2 validation
therefore loosens from "top-level def" to "top-level def or a
top-level import binding of the declared name". Plain assignments
stay invalid.

## D011 - the suppression gate covers Bash (stateless diff) (2026-07-15)

Rules affected: none. This is a `tackbox hook` contract (as D005 is a
helper contract); the hook is the enforcement surface.

Decision: the hook gains a Bash arm. On a PostToolUse event for the
Bash tool (same guard: cwd inside a git repo with dev.py at its
root), the hook compares the working tree against HEAD - tracked
modifications plus untracked files, .gitignore respected - and
applies per file the same gate the Edit arm applies per edit: the
marker multiset diff (more markers -> block; equal count but a
changed marker or reason -> block; fewer -> free) on lintable files
(D012), plus added `.tackbox-reporters` lines, unconditionally. A hit
returns the PostToolUse block decision carrying the same approval
wording as the PreToolUse gate: the marker and its file, and that a
new suppression marker needs explicit user approval - revert it or
get the approval.

Stateless by design: no ledger of approved markers; HEAD is the
approval record - an approved marker stops flagging once committed.
Worst mode is a repeated question about a not-yet-committed approved
marker on every later Bash call, never a silent pass. A rename of a
marker-bearing file re-asks (its markers are new against HEAD at the
new path): over-asks, never under.

The asymmetry with the Edit arm, named: PreToolUse asks before a
marker lands; a Bash command's effect is observable only after it
ran, so the Bash arm asks after the fact - containment, not
prevention. The diff is command-agnostic: sed, echo, a heredoc or
python -c all land in the same worktree diff. Infra failures (git not
answering) follow the hook's existing non-blocking contract (exit 1 +
stderr); the approval gate has no CI backstop, so review owns what an
infra failure lets through.

## D012 - marker gates ask only about lintable files (2026-07-15)

Rules affected: none. A `tackbox hook` contract; engine dispatch is
unchanged.

Decision: both marker-gate arms - PreToolUse (Edit / Write /
MultiEdit) and the D011 Bash arm - ask only about files the engine
dispatch would lint (extension match plus the engine's own path
filter, e.g. Go's testdata/ convention). A marker in a file no engine
lints is dead text: nothing reads it, so an approval question about
it is noise. The `.tackbox-reporters` gate is exempt from the
predicate and stays unconditional - the file itself is unlintable by
design.

The predicate is evaluated at mutation time against the destination
path. Planting a marker in a dead file (fixture.py.txt) is free and
stays dead; the move that brings it live (mv to fixture.py) makes its
markers new-against-HEAD at a lintable path, and the Bash arm asks
right there. Laundering is caught at the transition, statelessly.

Fixture space thus asks nothing: Go analyzer fixtures (dropped by the
Go engines' path filter) and non-lintable fixture extensions (java's
.java.txt) draw no questions, while consumer tests (`_test.go`,
`src/test/`, `test_*.py`, `*.test.js`) are lintable, so their markers
keep asking - test-skip suppression there is live. A path-name
exemption (a testdata/ segment) would be unsound wherever an engine
does lint such a path; lintability is sound by construction, and it
is the same predicate the post arm applies when it lints an edited
file - one scope, both sides of the hook.

Residual, named: a tackbox release that widens an engine's file set
can turn dead markers live without a mutation event - that is our own
release review's job; files generated at runtime by tests are outside
a static gate's model - escape-inventory tooling, not the gate, is
the net for both.

## D013 - tackbox escapes: the bypass surface in one command (2026-07-16)

Rules affected: none. This is the escapes-command contract; rules and
gates are unchanged.

Decision: `tackbox escapes` prints the repo's bypass surface as JSON
on stdout - the harness-agnostic interface. Entries: suppression
markers with their reasons, `.tackbox-reporters` declarations, and
notify / quiet call sites, each with file, line, and a context window
of surrounding source (default 3 lines, `--context N`). The scan
covers lintable files (the D012 predicate) plus the root
`.tackbox-reporters`; verb-site detection is textual per language -
an inventory may over-report, it is observability, not a lint.
`--since <rev>` prints only entries new against that revision, by
content identity (kind, file, text): over-reports on moved code,
never a silent drop. Exit 0 with entries present - the inventory is
not a gate; nonzero only for infra errors.

Rationale: the paved road is enforced by rules and gates; everything
that legitimately steps off it (a marker, a tier-2 declaration, a
quiet or notify lane choice) must be enumerable in one cheap command
that review tooling of any harness can consume.

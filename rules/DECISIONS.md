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

## D002 - per-name fingerprints for direct panic (2026-07-17)

Library contract, moved to docs/report-contracts.md (id kept). Not a
lint rule; its boundary with ERC006 arm 3 is recorded there.

## D003 - concurrency-isolated capture in go/report (2026-07-13)

Library contract, moved to docs/report-contracts.md (id kept).

## D004 - runtime helper capture APIs are tier-1 reporters (2026-07-17)

Rules affected: TBX001 (python swallow), JV001 (java swallow), JV005
(exit), JV006 (double capture), and every rule sharing capture
recognition.

Decision: the runtime capture helpers' public capture APIs are
recognized as tier-1 reporters: a consumer's catch that hands the
caught error to one is credited with no `no-report` marker and no
`.tackbox-reporters` entry - adopting the blessed path costs zero
lint ceremony. Tier-1 recognition covers direct reporting verbs. The
capture set is error / warn / quiet / panic in each language's naming;
quiet skips only the user lane, so it is a capture and carries the
dedupKey-shape validation where the engine has it. notify is never a
capture - whether notify terminates a failure path is D006's
separately gated decision.

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
as it counts slf4j and declared reporters.

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

## D010 - Python tier-1 recognition by import origin (2026-07-17)

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

The tackbox_report package itself (a tackbox_report path segment,
`_is_owner_file`) self-credits: its own top-level defs of the verbs are
the origin, so internal routing keeps its swallow credit (D004's marker
retirement stands). The tackbox_report package self-credits only its
direct reporting verbs. TBX010/TBX011 do not bind the tackbox_report
owner; they constrain consumer call sites, while the package owns its
internal direct-reporting implementation. It builds its own dedup keys
by design - report_panic's `panic:<name>` fingerprint (D002) is a
package-internal construction, not a consumer call site. Consumer repos
never lint the installed package, so the segment rule is inert outside
this repo.

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

## D011 - suppression approval is a committed manifest (2026-07-19)

Rules affected: none. This is a `tackbox hook` / `tackbox lint`
contract; rule behavior and marker semantics are unchanged.

Decision: approval of a suppression marker is an explicit, versioned
record: one line in `.tackbox/approvals` per approved occurrence -
address plus exact marker text (identity schema: D014). The
invariant is bidirectional and the check is a pure function of the
tree: every marker in the tree's inventory must be covered by an
entry, every entry must match a live marker (an orphan is an error),
and a file that refuses scope resolution is reported as
unresolvable, never guessed. One predicate serves every surface:
folded into `tackbox lint`'s verdict (findings semantics, nonzero
exit - CI and `dev.py check` inherit the wall), the standalone
`tackbox approvals` subcommand (0 consistent / 2 inconsistent / 1
infra; `--draft` emits entry lines for uncovered markers and gates
nothing), and the hook's Post arms (an edit tool reports a hit as
the lint arm does, block lines on stderr and exit 2; a Bash event
returns the top-level block decision). The check always covers the
whole tree regardless of lint scope - a scope-following check would
be a bypass for any consumer whose CI lints scoped; its lint section
is always headed `approvals (whole tree):`.

The approval act rides on the manifest itself: an edit adding a
manifest line draws the PreToolUse ask, quoting the entry; removals
are free; a multi-entry addition draws one all-or-nothing ask (the
permission decision is per-edit and indivisible). Markers in code
are free text: planting one - Edit, sed, merge, anything - never
asks; it makes the tree inconsistent, which every subsequent hook
event, `dev.py check`, and CI reports statelessly. A commit changes
nothing: the wall survives `--no-verify` and session ends. Checkout
or merge of a branch whose markers are covered by its own manifest
is silent - approvals travel with the tree.

Marker inventory, one definition, two precisions: the tree inventory
(the check) is AST-precise - comment nodes whose text matches the
marker regex, so a lookalike inside a string literal does not
participate; the Pre gate's added-line detection over edit fragments
is textual (fragments do not parse), a strict superset - the gap
costs an extra look at a lookalike, never a silent pass.

Rationale: approval inferred from events (HEAD position - committed
means approved - or hook-event sequences) re-asks about
approved-but-uncommitted markers and cannot see writes that bypass
the tool loop; an explicit record is consistent the moment the line
lands, needs no state, and puts every approval in the diff where
review reads it.

Residuals, named: (A1) the manifest records the claim of approval,
not proof of consent - a forged line is review's to catch, as a
loud, attributable diff; (A2) in permission modes that auto-approve
edits (`bypassPermissions`, possibly `acceptEdits`) the ask may not
surface - the line still lands as a visible diff, review owns as A1;
(A3) relocation of an approved marker within its scope (within the
file, for file-scope markers) is undetected - identity is
scope-grained, and a reason lying about its new context is review's
signal; (A4) an unapproved marker still suppresses its finding until
resolved - every hook event names the inconsistency, and check/CI
hold the wall; (A5) the user's own terminal commits bypass agent
hooks - CI owns them; (A6) editing an anonymous body renames its
hash segment and re-asks everything beneath - over-ask by design;
(A7) engine-version drift can change resolved chains - the pin
(D015) plus fixtures make it a visible, reviewed bump; (A8) the
Svelte template HTML-comment marker suppresses within the whole
element that follows - wider than line-adjacent, accepted
deliberately.

## D012 - only lintable files' markers participate (2026-07-19)

Rules affected: none. A `tackbox hook` / `tackbox lint` contract;
engine dispatch is unchanged.

Decision: the marker inventory behind the approvals check (D011)
covers only files the engine dispatch would lint (extension match
plus the engine's own path filter, e.g. Go's testdata/ convention),
plus the Markdown lang marker in lintable Markdown. A marker in a
file no engine lints is dead text: nothing reads it, so it needs no
approval, and an entry for it would approve a no-op. The
`.tackbox-reporters` gate is exempt from the predicate and stays
unconditional - the file itself is unlintable by design.

The predicate is evaluated against the current tree. Planting a
marker in a dead file (fixture.py.txt) is free and stays dead; the
move that brings it live (mv to fixture.py) puts it in the inventory
uncovered, and the next hook event, `dev.py check`, or CI reports
it. Laundering is caught at the transition, statelessly.

Fixture space thus needs no entries: Go analyzer fixtures (dropped
by the Go engines' path filter) and non-lintable fixture extensions
(java's .java.txt) are inert, while consumer tests (`_test.go`,
`src/test/`, `test_*.py`, `*.test.js`) are lintable, so their
markers need approval - test-skip suppression there is live. A
path-name exemption (a testdata/ segment) would be unsound wherever
an engine does lint such a path; lintability is sound by
construction, and it is the same predicate the lint dispatch itself
applies - one scope everywhere.

Residual, named: a tackbox release that widens an engine's file set
can turn dead markers live without a mutation event - that is our
own release review's job; files generated at runtime by tests are
outside a static model - escape-inventory tooling, not the gate, is
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

## D014 - approval-manifest identity schema (2026-07-19)

Rules affected: none. The carrier-independent address schema behind
D011. All reads go through one provider seam
(`load_approvals(root)`); the committed file is the first backend,
not the contract - an external approval store would implement the
same schema and bring its own consent act.

Decision: an entry is
`<repo-relative path>#<scope-chain>: <exact marker text>`; without
`#<chain>`, the address is file scope (module-level code, Markdown
outside any heading). The exact marker text is the full
`keyword: reason` occurrence text; a changed reason is a different
entry. Multiplicity is repeated identical lines; document order
pairs the k-th occurrence with the k-th entry, and the tails beyond
the shorter side report as uncovered or orphaned.

A marker's scope is the innermost scope containing its byte
position; the gap between declarations belongs to the parent (a
comment line above a `def` addresses the parent). A chain joins
segments with `.`, innermost last. A segment is the declaration's
name; an anonymous scope contributes a content-hash segment
`<h1a2b3c4d>`: sha256 over the UTF-8 bytes of the anonymous body's
text with every maximal whitespace run collapsed to one ASCII space
and the ends stripped, lowercase hex, truncated to 8. The chain
continues through anonymous segments; named declarations inside
them keep their names.

Name synthesis, exactly two rules: a Go method's receiver type
prefixes its name (`Server.Handle`); a JS/TS anonymous function
assigned directly in a `const`/`let`/`var` declarator takes the
variable's name. Java method segments carry a parameter-type
signature `name(int,int)`, normalized by whitespace collapse only -
types as written in source. Markdown chains are the heading outline
(ATX + setext, built by a level stack); fenced code is inert.
Same-name siblings of any kind disambiguate by a document-order
ordinal: the first keeps the bare name, the k-th is `name@k`.

Encoding is injective: within a segment the characters
`\` `.` `#` `:` `@` are backslash-escaped; within the path only
`\` `#` `:` are (dots stay literal). An entry line splits at the
first unescaped colon-space; the address splits at the first
unescaped `#`.

Svelte: the top-level script blocks are located by an html parse of
the file - never regex - and resolved as JS/TS per the `lang`
attribute with byte offsets mapped back; chains inside follow the
JS/TS rules. Markers outside script anchor at file scope; `<style>`
content takes no markers (no rule dispatches there). A file whose
language parse has ERROR nodes refuses resolution: its markers and
entries report as unresolvable, never guessed; for `.svelte` only
the extracted scripts' parse counts - ERROR nodes in the html
container parse are expected on every real component and exempt.

Named gaps: an anonymous-body edit renames the hash segment (D011
A6); inserting an earlier same-name sibling renumbers later
ordinals - accepted last-resort churn; Svelte snippet blocks are not
yet named scopes - file scope is the coarse address.

## D015 - outline engine: ast-grep, pinned, behind a seam (2026-07-19)

Rules affected: none. The tooling decision behind D014's scope
resolution.

Decision: scope outlines come from ast-grep (`ast-grep-cli==0.44.1`,
a runtime dependency of the thin wheel; the canonical executable is
`ast-grep`, never the `sg` alias - upstream warns it collides with
the Linux setgroups utility) invoked as a subprocess with `--json`
behind an internal contract: declarations and comments with ranges
per file, chain assembly in Python by range containment. Swapping
the engine is a local change; the manifest format encodes nothing
engine-specific. Per-language rule sets are strictly separate - a
kind unknown to a grammar zeroes the whole rule, so rule-set
validity is pinned by fixtures per language. Svelte is not an
ast-grep language and gets no custom grammar: the html container
parse plus script extraction (D014) covers it, and since ast-grep
does not recognize the `.svelte` extension (such paths silently
match nothing), Svelte content is always fed via stdin with an
explicit language. The pin makes resolution deterministic; a
version bump is a reviewed change (D011 A7). `tackbox doctor`
verifies presence and the pinned version.

Rejected: ANTLR - grammar decay across the five target languages,
10-30x Python runtime, build coupling, error recovery unfit for
dirty worktrees, v5 unready. py-tree-sitter with the language
pack - its Svelte grammar returns script content as raw_text (the
same two-phase work, no gain) and reintroduces per-platform grammar
binaries via a runtime download - more dependency and less
determinism.

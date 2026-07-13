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

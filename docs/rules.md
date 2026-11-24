# Cross-language rules

The per-language rule references are [Go](../go/README.md),
[Java](../java/README.md), [Python](../py/README.md), and
[JS / TS / Svelte](../js/README.md#rules). This file holds what spans
languages.

## Core policy

Every language applies the same model; [Go](../go/README.md#rules) shows it
rule by rule with examples.

- Every failure path (a Go err-branch, a `catch` / `except`) must propagate,
  capture, or carry an explicit `no-report: <reason>` marker.
- A parse result that falls back to a default must capture or carry
  `parse-skip: <reason>`.
- A terminal exit must be preceded by a capture or carry `no-report` (Go,
  Java); JS and Python flag an exit inside a catch.
- A bare nil return (Go) must carry `nil-return: <reason>` or use a
  `(val, ok)` / `(val, err)` result.
- A single failure path may not both capture and propagate (Go, Java, JS).
- The dedupKey must be a well-formed literal; in Go, capture-call
  arguments must additionally not carry raw user input (a
  `*http.Request` field).
- A `notify` (user lane only, no capture) may terminate a failure path
  only when it is narrowed: a narrow catch type (Java/Python) or an
  additional condition inside the branch (Go/JS). An unconditional
  notify in a broad catch routes every failure to a toast and blinds
  telemetry - a finding. A single path may not both capture and notify
  (error/warn already reach the user, so the pair double-shows). A
  `notify` is validated like a capture: static-literal msg, well-formed
  literal dedupKey.
- A skipped test must state a reason in every language (skip / todo / xfail /
  `@Disabled`); focused tests (`it.only`, `fit`) are an unconditional error.

## Duplication (DUP001, DUP002, DUP003)

Duplication is checked across Go, Python, Java, and the JS family (`.js`,
`.jsx`, `.mjs`, `.cjs`, `.ts`, `.tsx`, `.svelte`); Markdown is excluded,
since prose repetition is not a defect.

- **DUP001** flags a duplicated block - a clone of at least 50 tokens.
  Both ends are reported, each a finding at its own site, naming the
  counterpart block and the token count.
- **DUP002** flags a native `jscpd:ignore` marker. That channel would
  bypass the gated suppression below, so its presence alone is a
  finding; remove it.
- **DUP003** flags a `dup-ok` marker that became unnecessary because
  its callable-header clone is filtered automatically. Remove the
  marker and its matching approval together.

A pair repeated wholly inside two callable headers is filtered
automatically when both complete clone endpoints fit reliable syntax
boundaries ([D019](../rules/DECISIONS.md#d019---callable-header-duplicate-pairs-are-filtered-structurally-2026-07-25)).

Suppress one clone with a standalone `// dup-ok: <reason>` comment
directly above the block - a `#` or a single-line `/* ... */` comment
works per language. The reason must be at least 10 characters (D009),
and a trailing comment after code does not count. `dup-ok` above one
end drops only that end; above both ends it drops the whole clone.
Use it only for justified clones that survive automatic header
filtering. A matching approval confirms that a marker is authorized;
it does not make a redundant marker valid, so DUP003 remains until
both source marker and approval are removed.

## Reporter recognition

Capture helpers are recognized by origin, not by name - tier-1; see
[Go](../go/README.md#capture-and-propagation),
[JS / TS / Svelte](../js/README.md#reporter-recognition),
[Java](../java/README.md#capture-recognition), and
[Python](../py/README.md#capture-recognition).
Every language also honors a function declared in a repo-root
`.tackbox/reporters` file (`file#function: reason`) - tier-2. A
declaration names a report sink - it is not an exclude: it disables no
rule, and a declared call is honored only when the caught error flows
into its arguments. Declarations are validated every run - a dead file or
symbol is a hard error.

A `[usage]` declaration (`file#function [usage]: reason`) names the
opposite lane: a deliberate user-facing diagnostic exit, e.g. a CLI
`usage()` helper. It is never a capture. Its calls are clean outside
err-branches (nothing failed - no marker needed) and a finding inside
one (wrong sink for a failure path), regardless of arguments. Only
erclint (ERC003) consumes usage sinks, so a `[usage]` declaration on a
non-Go file is rejected - a dead line would be silent.

# Suppression markers, approvals, and excluded code

The README's [Exceptions](../README.md#exceptions) section is the overview;
this file holds the details.

## Marker forms

Every marker is `<keyword>: <reason>` carried by the language's
ordinary comment token:

- Go, Java, JS/TS: a `//` line comment. Block comments are never
  markers; the one exception is `dup-ok`, where the duplication
  engine also accepts a standalone single-line `/* ... */` (see
  [duplication](rules.md#duplication-dup001-dup002-dup003)).
- Python: a `#` comment.
- Svelte: inside `<script>` blocks the `//` form works as in JS/TS;
  the template adds two forms - a `//` comment inside a `{...}`
  expression (line-adjacent, as ever) and an HTML comment
  immediately above an element (see
  [Svelte template markers](../js/README.md#svelte-template-markers)).

Placement per rule (above the `try`, standalone above the block,
directly above the statement) is each rule's own contract; the
10-character reason floor (D009) is universal.

The keywords are `no-report`, `parse-skip`, `nil-return`, `test-skip`, and
`dup-ok`. `long-comment` is a marker keyword too, but no rule honors it: a
`long-comment:` comment suppresses nothing, yet it needs an approval entry
like any marker and is listed by `tackbox escapes`.

## Approval manifest

The manifest format and example are in the
[README](../README.md#approval-manifest).

The chain walks functions, classes, methods, or Markdown headings,
joined by `.`; an entry with no `#` sits at file scope. Anonymous
scopes (lambdas, arrows, IIFEs) appear as 8-hex content hashes; Java
overloads carry a parameter-type signature; same-name siblings take
an `@k` ordinal. Repeat the line for each identical occurrence.

For `tackbox lint` and `tackbox approvals`, the bidirectional check covers the whole tree:
a marker without a covering entry and an entry without a live marker
(an orphan) are both findings, reported by `tackbox lint` under the
`approvals (whole tree):` header whatever the lint scope.
`tackbox approvals` runs the same check standalone;
`tackbox approvals --draft` prints a ready entry line for every
uncovered marker - the address is computed for you, so approving a
marker you just wrote is one append away, and bootstrapping a repo
that already carries markers is: generate, review line by line,
commit.

Only markers in files an engine would lint participate in the check
(D012): a marker in a Go `testdata/` path or a non-lintable fixture
extension (a `.java.txt`) is dead text - no entry needed, no
question - while the `.tackbox/reporters` gate stays unconditional.

Approving is adding the line. In an agent session the edit that adds
a manifest line draws the PreToolUse ask quoting the entry (several
lines in one edit draw one all-or-nothing ask), so the only route to
a green check passes through a visible diff and a human decision.
Writing a marker itself never asks - by any channel, Edit or shell.
New session debt blocks the next edit outside the affected marker files
and `.tackbox/approvals`; post-edit feedback reports debt only in touched
files. Bash and eval run no approvals checks. `dev.py check` and CI still
report all tree inconsistencies. Removing a manifest line is free; a
marker whose text, scope, or count changes needs its entry updated.

Approvals messages carry the suppressed rule's text, or say the marker
suppresses nothing when no finding is available, such as in the standalone
`approvals` command. Each inconsistency takes one line; no message supplies
an approval entry to copy. Suppressed findings stay out of ordinary lint
output, its exit status, and codequality. See D020 in
[rules/DECISIONS.md](../rules/DECISIONS.md) for the session boundary.

## Generated and vendored code

Committed code that carries a generated or vendored git attribute is
excluded from the whole lint - findings there are not fixable in the
file (they belong in the generator), and a suppression marker cannot
survive regeneration. tackbox honors exactly three attributes,
`linguist-generated`, `gitlab-generated`, and `linguist-vendored`,
read from `.gitattributes` the same way the host (GitHub, GitLab)
reads them - no exclude surface of tackbox's own.

A file is excluded when `git check-attr` reports one of the three as
set. For the [README example](../README.md#generated-and-vendored-code),
`gen/**` excludes everything under `gen/`; `vendor/**` the same for
vendored. `=false` re-includes a single file inside an excluded tree
(`gen/keep.go` is linted normally); `-attr` and `!attr` also leave a file in.
Only `set` / `=true` excludes.

The exclusion covers everything: per-file engines, the erclint Go
package run (a mixed package's excluded file is compiled but its
findings drop; a compile break still fails loudly), duplication, the
CodeClimate report, and the marker inventory - an excluded file's
markers are dead, so a manifest entry addressing one orphans. A lint
run whose scope touches excluded files prints one summary line:

```text
excluded by attributes: 12 files in scope (tackbox escapes lists all)
```

It counts unique excluded files in the current scope (absent at zero),
so scoped runs are not wallpapered with a global constant;
`tackbox escapes` lists the full population as `attribute-excluded`
entries.

Because the excluded population is where the lint, the marker
inventory, and host diff review are all blind, the agent hook makes
the two ways into it loud:

- adding a positive exclusion line (a bare `<attr>` or `<attr>=true`)
  to any `.gitattributes` draws a PreToolUse ask, one joint ask per
  edit listing every added line; removals, `=false`, `-attr`, and
  non-exclusion lines are free;
- editing (or creating) a file that is effective-excluded draws an
  ask naming the attributes.

Generators run through Bash and are unaffected - the boundary is that
the change stays in the commit/PR diff, and hosts collapse
excluded-file diffs, so reviewers must expand them.

`tackbox doctor` adds an informational `attributes` section (never a
check, no exit-code effect) naming local conditions that can make a
run diverge from a clean CI clone - an `info/attributes` or an
untracked/index-hidden `.gitattributes` carrier mentioning the
attributes, or a neutralized attribute source override.

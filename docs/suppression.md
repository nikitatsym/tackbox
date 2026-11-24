# Suppression markers, approvals, and excluded code

The README's [Exceptions](../README.md#exceptions) section is the overview;
this file holds the details.

## Markers

A marker is `<keyword>: <reason>` carried by the language's
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

The keywords that suppress a finding are `no-report`, `parse-skip`,
`nil-return`, `test-skip`, and `dup-ok`. Placement per rule (above the `try`,
standalone above the block, directly above the statement) is each rule's own
contract. A marker suppresses only with a reason of at least 10 characters
(D009).

The approvals check and `tackbox escapes` take in every comment that starts
with one of those keywords or with `long-comment:`, whatever its reason length,
so each needs a manifest line. `long-comment` suppresses no rule.

## Approval manifest

The format and example are in the [README](../README.md#approval-manifest);
`--draft` and the exit codes are under [Commands](../README.md#commands).

The chain walks functions, classes, methods, or Markdown headings,
joined by `.`; an entry with no `#` sits at file scope. Anonymous
scopes (lambdas, arrows, IIFEs) appear as 8-hex content hashes; Java
overloads carry a parameter-type signature; same-name siblings take
an `@k` ordinal. Repeat the line for each identical occurrence.

Every marker needs a covering entry and every entry a live marker; an entry
without one is an orphan. `tackbox lint` reports both under an
`approvals (whole tree):` header. To adopt tackbox in a repo that already
carries markers, generate the lines with `tackbox approvals --draft`, review
them line by line, and commit.

Only markers in files an engine would lint participate in the check
(D012): a marker in a Go `testdata/` path or a non-lintable fixture
extension (a `.java.txt`) is dead text - no entry needed, no
question - while the `.tackbox/reporters` gate stays unconditional.

Approving is adding the line, so a green check passes through a visible
diff and a human decision. Writing a marker never asks by itself. Removing a
manifest line is free; a marker whose text, scope, or count changes needs its
entry updated. What an agent session may edit while approvals are
inconsistent: [agent hook](agent-hook.md#session-and-gates).

Approvals messages carry the suppressed rule's text, or say the marker
suppresses nothing when no finding is available, such as in the standalone
`approvals` command. Each inconsistency takes one line; no message supplies
an approval entry to copy. Suppressed findings stay out of ordinary lint
output, its exit status, and codequality. See D020 in
[rules/DECISIONS.md](../rules/DECISIONS.md) for the session boundary.

## Generated and vendored code

Findings in generated or vendored files are not fixable in the file (they
belong in the generator), and a marker cannot survive regeneration, so tackbox
reads the host's own attributes from `.gitattributes` the way GitHub and
GitLab do, with no exclude surface of its own.

A file is excluded when `git check-attr` reports one of the three attributes
as set. For the [README example](../README.md#generated-and-vendored-code),
`gen/**` excludes everything under `gen/`; `vendor/**` the same for
vendored. `=false` re-includes a single file inside an excluded tree
(`gen/keep.go` is linted normally); `-attr` and `!attr` also leave a file in.
Only `set` / `=true` excludes.

The exclusion covers everything: per-file engines, the erclint Go
package run (a mixed package's excluded file is compiled but its
findings drop; a compile break still fails loudly), duplication, the
CodeClimate report, and the marker inventory - an excluded file's
markers are dead, so a manifest entry addressing one orphans. A lint
run whose scope touches excluded files prints one summary line counting
the excluded files in that scope (absent at zero):

```text
excluded by attributes: 12 files in scope (tackbox escapes lists all)
```

The agent hook asks before an edit adds an exclusion or touches an excluded
file ([agent hook](agent-hook.md#session-and-gates)). Generators run through
Bash and are unaffected; hosts collapse excluded-file diffs, so reviewers must
expand them.

`tackbox doctor` adds an informational `attributes` section (never a
check, no exit-code effect) naming local conditions that can make a
run diverge from a clean CI clone - an `info/attributes` or an
untracked/index-hidden `.gitattributes` carrier mentioning the
attributes, or a neutralized attribute source override.

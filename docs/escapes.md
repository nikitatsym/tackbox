# Escapes inventory

`tackbox escapes` prints the repo's bypass surface as JSON on stdout, for
review tooling (D013); exit codes are under [Commands](../README.md#commands).
It lists:

- **suppression markers** - every marker keyword
  ([keywords](suppression.md#markers)) followed by a colon, each with its
  reason. Matching is textual over the raw source, so a keyword inside a
  string is listed too, though it is no marker and needs no approval;
- **`.tackbox/reporters` declarations** - the tier-2 sinks;
- **notify / quiet lane choices** - the call sites of the user-lane-only
  `notify` and the telemetry-only `quiet` verbs;
- **attribute-excluded files** - one entry per set generated or vendored
  attribute.

## JSON contract

```json
{
  "since": null,
  "entries": [
    {"kind": "attribute-excluded", "file": "gen/api.pb.go",
     "attribute": "linguist-generated"},
    {"kind": "marker", "file": "a/b.py", "line": 12,
     "text": "no-report: central boundary already captures it",
     "reason": "central boundary already captures it",
     "context": ["...", "...", "..."]},
    {"kind": "reporter-decl", "file": ".tackbox/reporters", "line": 2,
     "text": "src/app/errors.py#report_api_error: the API sink",
     "context": ["..."]},
    {"kind": "notify-site", "file": "js/foo.js", "line": 40,
     "text": "notify('offline', err, {}, 'net.offline')",
     "context": ["..."]},
    {"kind": "quiet-site", "file": "go/x.go", "line": 9,
     "text": "report.Quiet(ctx, ...)", "context": ["..."]}
  ],
  "counts": {"marker": 1, "reporter-decl": 1, "notify-site": 1,
             "quiet-site": 1, "attribute-excluded": 1}
}
```

- `counts` always carries all five kinds, even at zero, so consumers see a
  stable shape. Every count is an entry count except `attribute-excluded`,
  which counts unique files.
- `since` echoes the `--since` rev, or `null`.
- `attribute-excluded` entries carry only `kind` / `file` / `attribute` (no
  line or text): the whole file is the bypass, one entry per set attribute
  of the three (`linguist-generated`, `gitlab-generated`,
  `linguist-vendored`). See
  [generated and vendored code](suppression.md#generated-and-vendored-code).
- `text` is the trimmed source line; for a marker it runs from the marker
  keyword to end of line.
- `reason` (markers only) is what follows the keyword's colon, trimmed -
  possibly empty.
- `context` is the surrounding source, `--context N` lines each side
  (default 3), inclusive of the entry line itself - the window
  `[line-N, line+N]`, clipped at file edges, each line trimmed of trailing
  whitespace. It is plain source; the entry line is not marked.
- `entries` are sorted by `(file, kind, kind-subkey)` - the subkey is
  `(line, text)` for the line-bearing kinds and `(attribute,)` for
  `attribute-excluded`.

## Scope and detection

The scan covers the same lintable source set the linter would scan (the
D012 predicate: extension match plus each engine's path filter, so a Go
`testdata/` file is out) minus the attribute-excluded files, plus the root
`.tackbox/reporters` (every non-empty line is one declaration - the file has
no comment syntax). An attribute-excluded file's own markers are dead, so it
surfaces only as its `attribute-excluded` entries.
notify / quiet call sites are detected **textually per language**
(`report_quiet` / `notify` in Python, `reportQuiet` / `notify` in the JS
family, `.Quiet(` / `.Notify(` in Go, `.quiet(` / `.notify(` in Java),
word-boundaried so `notifyAll(` does not match. Textual detection can
over-report (a match inside a comment or string counts).

## `--since <rev>`

`--since <rev>` prints only entries **new against `<rev>`**, compared by
content identity (`(kind, file, text)`, or `(kind, file, attribute)` for
attribute-excluded): the same extraction runs against the tree at `<rev>` and
is subtracted, count aware, from the current tree's entries. Attributes are
resolved as of `<rev>`, so an attribute added since the rev reports its
newly-excluded files, a removed one re-activates its markers as new (never a
silent subtraction), and an unchanged one adds no noise. This needs
git >= 2.40; the plain listing does not. It over-reports on moved code (a new
file path is a new identity) but never silently drops an entry.

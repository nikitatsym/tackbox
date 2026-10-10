# erclint (Go)

Error reporting coverage, test integrity, and unused binding analyzers
for Go. Implements the rules described in the parent README and source spec.

## Usage

erclint ships inside the `tackbox` wheel and is run by the CLI - there
is no separate install:

```bash
uvx tackbox@latest lint .
```

## Rules

| Code   | Name          | Summary                              |
|--------|---------------|--------------------------------------|
| ERC001 | errcheck      | err branch must propagate or capture |
| ERC002 | parsenil      | parser err: capture, propagate, mark |
| ERC003 | terminal      | Fatal/Exit/die must capture or report|
| ERC004 | returnnil     | bare nil return needs marker or pair |
| ERC005 | doublecapture | no capture and `return err` together |
| ERC006 | fingerprint   | no raw input; static msg; dedupKey   |
| ERC007 | recoverswallow| recover must report or re-panic      |
| ERC008 | skiptest      | skipped test must state a reason     |
| ERC009 | notifygate    | notify must be narrowed (condition)  |
| ERC010 | unused        | remove unused unexported declarations |
| ERC011 | unusedparams  | remove unused private-function parameters |

Details per rule:

- ERC001 `errcheck` - in any `if err != nil` branch (guarding an
  error-assignable identifier - a bare `int`/`*Conn` guard is not an
  err-branch), propagate the error, capture it, carry it into a
  printing terminal, or carry `// no-report: <reason>`. Propagation is
  the err OBJECT reaching a returned error-assignable value: a bare
  `return err`, a `%w` wrap, `errors.Join`, or a wrapper composite /
  constructor carrying it (`&E{Cause: err}`, `newE(err)` - the wrapper's
  Unwrap contract is trusted, not verified). The chain breaks - a
  rethrow without cause - only when every occurrence of err in the
  returned value is stringified (`%v`, `.Error()`, `string(...)`). A
  two-step wrap (`w := fmt.Errorf("...%w", err); return w`) is credited.
  Returning the original error inside a diagnostic struct also counts as
  passing it to the caller. The linter checks that the same error reaches an
  `error`-typed field in the returned value on every returning path. Passing
  it to an already declared capture handler also counts. Creating a struct
  or naming a field `Cause` is not enough: discarded values, overwritten
  errors, and fields containing only text or `any` do not count. If the linter
  cannot prove that a helper preserves the error, it does not assume so.
  This proves that the error is passed on, not that it is shown to the user.
- ERC002 `parsenil` - parser results that fall through to nil must
  capture, propagate the error (same object-flow rule as ERC001), or
  carry `// parse-skip: <reason>`.
- ERC003 `terminal` - `log.Fatal*`, `os.Exit`, project-local `die`
  must be preceded by a capture, carry the error into their own
  arguments (`log.Fatal(err)`, a reported death), resolve to a declared
  sink, or carry `// no-report: <reason>` (e.g. the normal `os.Exit(0)`
  at the end of `main`). A declared `[usage]` sink is the opposite,
  single-purpose lane: clean outside err-branches, a finding inside
  one regardless of arguments.
- ERC004 `returnnil` - bare `return nil` on `*T`/`[]T`/`map` needs
  `// nil-return: <reason>` or use `(val, ok)` / `(val, err)`.
- ERC005 `doublecapture` - a single err-branch may not both capture and
  `return err`; nor may one execution path both capture and `Notify`
  (D006 double-lane - error/warn already reach the user lane, so the
  pair double-shows). The double-lane arm is path-sensitive: exclusive
  if/switch legs do not pair.
- ERC006 `fingerprint` - capture-call arguments may not carry raw user
  input; the user-lane msg of `Error`/`Warn`/`Notify` must be a static
  string literal (D007); and the dedupKey of `Error`/`Warn`/`Quiet`/
  `Notify` must be a well-formed literal `area.suffix[:id]` (D008).
  `Notify` is validated here but is never credited as a capture.
- ERC007 `recoverswallow` - a `recover()` must report the recovered
  value (to `go/report` or a declared sink that receives it) or
  re-panic; a bare recover-and-continue needs `// no-report: <reason>`.
- ERC008 `skiptest` - in `_test.go`, a `Skip`/`Skipf` on
  `testing.T`/`B`/`F` must carry a non-empty reason argument
  (non-literal arguments are trusted); a bare `SkipNow()` needs
  `// test-skip: <reason>` directly above. Resolution is by origin: a
  local type's own `Skip` method is not a test skip.
- ERC009 `notifygate` - a `go/report.Notify` carrying the caught error
  may terminate an err-branch only when it sits under an additional
  condition (an `if`/`switch` inside the branch); an unconditional
  notify as the sole handling of the branch routes every failure to the
  user lane and blinds telemetry. The complement of a narrowed notify
  stays covered by ERC001; a `// no-report: <reason>` marker above the
  branch suppresses.

`_test.go` files are skipped by ERC001-007 and ERC009. ERC008 checks
test skips, and ERC010-011 check unused declarations and parameters in tests.

### Unused declarations (ERC010)

ERC010 uses staticcheck's U1000 package reachability analysis to find unused
unexported functions, methods, types, fields, constants, and package-level
variables. Diagnostics use `ERC010: <kind> <name> is unused (U1000)` in both
text and JSON output; the JSON analyzer name is `unused`.

Exported declarations are treated as API and are not reported. Function
parameters are outside this rule's scope. The Go compiler already rejects
unused local variables and imports. Naming conventions and the no-suppression
policy are defined in [D021](../rules/DECISIONS.md#d021---unused-bindings-are-a-mandatory-lane-2026-10-10);
staticcheck `lint:ignore` and `lint:file-ignore` directives also have no effect.

Analysis loads the complete build-selected package, including sibling files
excluded from tackbox's source set. Those files can therefore establish uses;
tackbox filters their diagnostics from the lint output rather than removing
them from Go's package. A hook editing one file also analyzes the complete
package. Callers must pass package patterns, not a single `.go` filename
that discards its sibling context.
Directories whose Go files are all excluded by the current build constraints
are not package targets and are skipped. Package compile errors still fail
the engine run.

A production declaration used only by tests is still reported: test-only
helpers belong in `_test.go`. Production findings appear once, not once per
test variant. Generated-code reachability and other exemptions follow U1000;
this is not a whole-program proof that an exported API is used.

ERC010 analyzes only the host GOOS and current architecture and build tags.
A helper in a platform-neutral file used only by platform-specific callers
can therefore be reported on another host. Move the helper next to its callers
under the matching build constraint; do not rename it to hide the finding.

### Unused parameters (ERC011)

ERC011 adapts gopls v0.23.0's `unusedparams` analyzer. It checks unexported
function and method declarations used in the package, including test helpers.
Exported declarations, function literals, functions used as values, fixed ABI
roots (`go:linkname`, cgo `export`, `go:wasmexport`), generated code, body-less
declarations, empty or panic-only bodies, and blank or unnamed parameters are
excluded. An unexported method sharing a name with a package interface method
is conservatively excluded, even when the signatures differ. Generic method
uses are matched to their origin, including instantiated method values.
Function literals are excluded because an explicit function type can require
their signature.

The diagnostic is `ERC011: unused parameter <name>`, followed by the remedy:
remove the parameter and the matching argument at every call site in this
package. Preserve argument side effects and their original evaluation order:
Go evaluates arguments left to right, and later arguments or captured values
may depend on earlier ones. Use `_` only when another build variant needs
the signature. There is no automatic removal fix.

Tackbox's JSON engine boundary selects the loaded package variant that includes
internal tests. Its presence suppresses plain-package ERC011 findings even
when it has no findings. This preserves signatures used as function values
only by internal tests. External test packages do not suppress production
findings. `-test=false` and build tags determine which variants are loaded;
the boundary does not infer test variants from files on disk.
Each raw ERC011 finding carries `variant` (`plain`, `internal-test`, or
`external-test`). Internal-test presence is encoded under the exact package
path and `unusedparams` as
`{"record":"erc011-package-variant-v1","variant":"internal-test"}`.
The boundary merges result documents and removes presence records and variant
fields before caching, filtering, or rendering. Analyzer errors remain visible
and fail the run. Standalone non-JSON erclint is outside this guarantee and
can report a plain-variant false positive.
Package/type-check failures produce an engine error and failing exit without a
Python traceback; CodeClimate output is still written for valid findings.

Declarations with no references are left to ERC010; dead recursive functions
and callees reached only by dead code can get both ERC010 and ERC011.
Remove that dead code rather than editing its parameters. ERC011 has the
same host and build-configuration limits as [ERC010](#unused-declarations-erc010).
Hook presentation follows the [agent hook contract](../README.md#agent-hook);
full repository gates cover findings outside the hook's target and line scope.

The linked x/tools supports Go 1.27.2 export data.

## Markers

Markers must appear on the line immediately above the branch or
return they apply to and must carry a reason of at least 10 characters
(non-empty was too cheap - `ok` / `todo` passed).

```go
// no-report: caller already wraps and captures
if err != nil {
    return err
}

// no-report: normal exit
os.Exit(code)

// parse-skip: optional-config
v, _ := strconv.Atoi(os.Getenv("MAX"))

// nil-return: caller treats nil as empty
return nil

// test-skip: covered by the integration suite instead
t.SkipNow()
```

In `-json` mode, erclint emits suppressed findings separately from normal
analysis diagnostics, with the same rule text plus `suppressed`,
`marker_kind`, and `marker_line`. The duplication wrapper uses the same
fields in its `--machine` output. Suppressed findings do not fail lint or
appear in ordinary lint output or codequality; only the approvals renderer
presents them (D020 in [../rules/DECISIONS.md](../rules/DECISIONS.md)).

## Capture and propagation

A call counts as a capture by origin, never by name. erclint uses type
information: the callee must resolve to
`github.com/nikitatsym/tackbox/go/report` and be a recognized export -
`Error` / `Warn` / `Quiet` (error-capture) or `Panic` (panic-capture).
`Notify` is the user-lane-only verb: never a capture, gated by ERC009
and validated (msg/dedupKey) by ERC006. Other exports of that package
(`Init`, `Flush`, `Crumb`, ...) are not captures, and a bare local
`Error(...)` that merely shares the name is not trusted.

A repo may also declare its own sinks in a root `.tackbox/reporters`
file (`file#function: reason`); a declared call counts only when the
caught error flows into its arguments. Declarations are validated every
run - a dead file or symbol is a hard error.

Propagation means `return ..., err`, `return err`, or `panic(err)`
referencing the err identifier.

User-input expressions banned in capture arguments: `r.URL.Path`,
`r.Header.Get(...)`, `req.Body` (and equivalent `*http.Request`
fields under any receiver name).

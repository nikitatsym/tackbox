# erclint (Go)

Error reporting coverage analyzer for Go. Implements the rules
described in the parent README and the source spec.

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
| ERC006 | fingerprint   | capture args may not name secrets    |
| ERC007 | recoverswallow| recover must report or re-panic      |
| ERC008 | skiptest      | skipped test must state a reason     |

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
- ERC005 `doublecapture` - a single err-branch may not both capture
  and `return err`.
- ERC006 `fingerprint` - capture-call arguments (message, tags,
  dedupKey) may not name secrets or carry raw user input.
- ERC007 `recoverswallow` - a `recover()` must report the recovered
  value (to `go/report` or a declared sink that receives it) or
  re-panic; a bare recover-and-continue needs `// no-report: <reason>`.
- ERC008 `skiptest` - in `_test.go`, a `Skip`/`Skipf` on
  `testing.T`/`B`/`F` must carry a non-empty reason argument
  (non-literal arguments are trusted); a bare `SkipNow()` needs
  `// test-skip: <reason>` directly above. Resolution is by origin: a
  local type's own `Skip` method is not a test skip.

`_test.go` files are skipped by every analyzer except ERC008
`skiptest`, whose subject is the tests themselves.

## Markers

Markers must appear on the line immediately above the branch or
return they apply to and must carry a non-empty reason.

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

## Capture and propagation

A call counts as a capture by origin, never by name. erclint uses type
information: the callee must resolve to
`github.com/nikitatsym/tackbox/go/report` and be a recognized export -
`SentryErr` / `Warn` (error-capture) or `Panic` (panic-capture). Other
exports of that package (`Init`, `Flush`, `Crumb`, ...) are not
captures, and a bare local `sentryErr(...)` that merely shares the name
is not trusted.

A repo may also declare its own sinks in a root `.tackbox-reporters`
file (`file#function: reason`); a declared call counts only when the
caught error flows into its arguments. Declarations are validated every
run - a dead file or symbol is a hard error.

Propagation means `return ..., err`, `return err`, or `panic(err)`
referencing the err identifier.

Fingerprint stop-words (case-insensitive substring match on
identifier names): `token`, `password`, `key`, `secret`, `cookie`.

User-input expressions banned in capture arguments: `r.URL.Path`,
`r.Header.Get(...)`, `req.Body` (and equivalent `*http.Request`
fields under any receiver name).

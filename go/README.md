# erclint (Go)

Error reporting coverage analyzer for Go. Implements the rules
described in the parent README and the source spec.

## Install

```bash
go install github.com/nikitatsym/tackbox/go/cmd/erclint@latest
erclint ./...
```

## Rules

| Code   | Name          | Summary                              |
|--------|---------------|--------------------------------------|
| ERC001 | errcheck      | err branch must propagate or capture |
| ERC002 | parsenil      | parser err must capture or mark      |
| ERC003 | terminal      | Fatal/Exit/die need capture above    |
| ERC004 | returnnil     | bare nil return needs marker or pair |
| ERC005 | doublecapture | no capture and `return err` together |
| ERC006 | fingerprint   | capture args may not name secrets    |

Details per rule:

- ERC001 `errcheck` - in any `if err != nil` branch, propagate,
  capture, or carry `// no-sentry: <reason>`.
- ERC002 `parsenil` - parser results that fall through to nil must
  capture or carry `// parse-skip: <reason>`.
- ERC003 `terminal` - `log.Fatal*`, `os.Exit`, project-local `die`:
  must be preceded by a capture call or carry `// no-sentry: <reason>`
  (e.g. for normal `os.Exit(0)` at the end of `main`).
- ERC004 `returnnil` - bare `return nil` on `*T`/`[]T`/`map` needs
  `// nil-return: <reason>` or use `(val, ok)` / `(val, err)`.
- ERC005 `doublecapture` - a single err-branch may not both capture
  and `return err`.
- ERC006 `fingerprint` - capture-call fingerprint args may not name
  secrets or carry raw user input.

`_test.go` files are skipped by every analyzer.

## Markers

Markers must appear on the line immediately above the branch or
return they apply to and must carry a non-empty reason.

```go
// no-sentry: caller already wraps and captures
if err != nil {
    return err
}

// no-sentry: normal exit
os.Exit(code)

// parse-skip: optional-config
v, _ := strconv.Atoi(os.Getenv("MAX"))

// nil-return: caller treats nil as empty
return nil
```

## Capture and propagation

Capture call names matched on the last identifier of the call
expression: `sentryErr`, `SentryErr`, `Warn`, `Panic`. So both bare
`sentryErr(...)` (gmux-style local helper) and `report.SentryErr(...)`
(from `github.com/nikitatsym/tackbox/go/report`) are recognized.
Propagation means `return ..., err`, `return err`, or `panic(err)`
referencing the err identifier.

Fingerprint stop-words (case-insensitive substring match on
identifier names): `token`, `password`, `key`, `secret`, `cookie`.

User-input expressions banned in capture arguments: `r.URL.Path`,
`r.Header.Get(...)`, `req.Body` (and equivalent `*http.Request`
fields under any receiver name).

# erclint (Go)

Error reporting coverage analyzer for Go. Implements the rules
described in the parent README and the source spec.

## Install

```
go install github.com/nikitatsym/tackbox/go/cmd/erclint@latest
erclint ./...
```

## Rules

| Code     | Name           | Summary                                                                 |
|----------|----------------|-------------------------------------------------------------------------|
| ERC001   | errcheck       | `if err != nil` branch: propagate, capture, or `// no-sentry: <reason>` |
| ERC002   | parsenil       | parser err: capture or `// parse-skip: <reason>`                        |
| ERC003   | terminal       | `log.Fatal*` / `os.Exit` / `die`: capture above, or `// no-sentry: <reason>` |
| ERC004   | returnnil      | bare `return nil` on `*T`/`[]T`/`map`: marker or use `(val, ok/err)`    |
| ERC005   | doublecapture  | capture and `return err` in the same err-branch                         |
| ERC006   | fingerprint    | capture args may not name secrets or raw user input                     |

`_test.go` files are skipped by every analyzer.

## Markers

Markers must appear on the line immediately above the branch or
return they apply to and must carry a non-empty reason.

```
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

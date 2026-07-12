// Package report is a testdata stub matching the capture package import path
// and surface so callee type-info resolves for tier-1 recognition. SentryErr
// and Warn take a variadic tail so fixtures can author wrong-arity calls the
// AST-count dedupkey rule flags; production SentryErr/Warn are fixed 5-arg
// (ctx, msg, err, tags, dedupKey).
package report

func SentryErr(args ...any) {}

func Warn(args ...any) {}

func Panic(name string, recovered any) {}

func Flush() {}

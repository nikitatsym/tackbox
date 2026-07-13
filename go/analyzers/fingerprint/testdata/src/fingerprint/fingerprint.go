package fingerprint

import (
	"context"
	"net/http"

	"github.com/nikitatsym/tackbox/go/report"
)

// bare local SentryErr shares the name but not the report origin: never a
// capture, never scanned - the regression the native port fixes runs the
// other way (qualified report.SentryErr WAS blind under opengrep).
func SentryErr(args ...any) {}

// ---- negatives: must not fire ----

func cleanBasic(ctx context.Context, err error) {
	report.SentryErr(ctx, "auth failed", err, nil, "auth.creds")
}

// tags map with domain nouns as string-literal keys/values, plus a dedupKey
// carrying the optional single :id - all literals, all clean.
func cleanTags(ctx context.Context, err error) {
	report.SentryErr(ctx, "agent tokens persist on create", err, map[string]string{"area": "tokens.persist", "store": "userkey"}, "tokens.persist:agent.create")
}

// Panic is a recognized reporter but carries no dedupKey: a clean 2-arg call.
func cleanPanic(recovered any) {
	report.Panic("worker", recovered)
}

// bare local SentryErr that does not resolve to the report package: it is
// never a capture, so it is never scanned.
func cleanBareLocal(ctx context.Context, err error, msg string) {
	SentryErr(ctx, msg, err, nil, "auth.creds")
}

// ---- dedupkey (tier-1 report.SentryErr/Warn only) ----

// wrong arity: dedupKey missing.
func arityWrong(ctx context.Context, err error) {
	report.SentryErr(ctx, "msg", err, nil) // want `ERC006: capture call must pass 5 args`
}

// dedupKey is a non-literal variable: the fingerprint would drift per call.
func dedupNotLiteral(ctx context.Context, err error, dk string) {
	report.SentryErr(ctx, "msg", err, nil, dk) // want `ERC006: dedupKey must be a string literal`
}

// dedupKey literal in the wrong format: uppercase area.
func dedupBadFormat(ctx context.Context, err error) {
	report.SentryErr(ctx, "msg", err, nil, "Auth.creds") // want `ERC006: dedupKey must match area.suffix`
}

// Warn is the other tier-1 helper: same dedupKey contract, no area.suffix here.
func warnBadFormat(ctx context.Context, err error) {
	report.Warn(ctx, "msg", err, nil, "auth") // want `ERC006: dedupKey must match area.suffix`
}

// ---- user-input (any recognized reporter) ----

// raw r.URL.Path as a capture arg.
func rawURLPath(ctx context.Context, err error, req *http.Request) {
	report.SentryErr(ctx, req.URL.Path, err, nil, "http.path") // want `ERC006: capture arg carries raw \*http.Request input \(r.URL.Path\)`
}

// raw r.Header.Get(...) under a different receiver name.
func rawHeaderGet(ctx context.Context, err error, httpReq *http.Request) {
	report.Warn(ctx, httpReq.Header.Get("X-Trace"), err, nil, "http.hdr") // want `ERC006: capture arg carries raw \*http.Request input \(r.Header.Get\(\.\.\.\)\)`
}

// raw r.Body as a capture arg.
func rawBody(req *http.Request) {
	report.Panic("http", req.Body) // want `ERC006: capture arg carries raw \*http.Request input \(r.Body\)`
}

// same-shaped selector on an unrelated type: type-aware detection keeps it
// clean (cfg is not *http.Request).
func cleanNonRequestSelector(ctx context.Context, err error) {
	var cfg struct{ URL struct{ Path string } }
	report.SentryErr(ctx, cfg.URL.Path, err, nil, "cfg.path")
}

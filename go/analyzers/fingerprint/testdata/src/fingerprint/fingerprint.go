package fingerprint

import (
	"context"
	"net/http"

	"github.com/nikitatsym/tackbox/go/report"
)

// bare local Error shares the name but not the report origin: never a
// capture, never scanned - the regression the native port fixes runs the
// other way (qualified report.Error WAS blind under opengrep).
func Error(args ...any) {}

// ---- negatives: must not fire ----

func cleanBasic(ctx context.Context, err error) {
	report.Error(ctx, "auth failed", err, nil, "auth.creds")
}

// tags map with domain nouns as string-literal keys/values, plus a dedupKey
// carrying the optional single :id - all literals, all clean.
func cleanTags(ctx context.Context, err error) {
	report.Error(ctx, "agent tokens persist on create", err, map[string]string{"area": "tokens.persist", "store": "userkey"}, "tokens.persist:agent.create")
}

// Panic is a recognized reporter but carries no dedupKey: a clean 2-arg call.
func cleanPanic(recovered any) {
	report.Panic("worker", recovered)
}

// bare local Error that does not resolve to the report package: it is
// never a capture, so it is never scanned.
func cleanBareLocal(ctx context.Context, err error, msg string) {
	Error(ctx, msg, err, nil, "auth.creds")
}

// ---- dedupkey (tier-1 report.Error/Warn only) ----

// wrong arity: dedupKey missing.
func arityWrong(ctx context.Context, err error) {
	report.Error(ctx, "msg", err, nil) // want `ERC006: capture call must pass 5 args`
}

// dedupKey is a non-literal variable: the fingerprint would drift per call.
func dedupNotLiteral(ctx context.Context, err error, dk string) {
	report.Error(ctx, "msg", err, nil, dk) // want `ERC006: dedupKey must be a string literal`
}

// dedupKey literal in the wrong format: uppercase area.
func dedupBadFormat(ctx context.Context, err error) {
	report.Error(ctx, "msg", err, nil, "Auth.creds") // want `ERC006: dedupKey must match area.suffix`
}

// Warn is the other tier-1 helper: same dedupKey contract, no area.suffix here.
func warnBadFormat(ctx context.Context, err error) {
	report.Warn(ctx, "msg", err, nil, "auth") // want `ERC006: dedupKey must match area.suffix`
}

// ---- user-input (any recognized reporter) ----

// raw r.URL.Path in a capture arg (a tags value; msg stays a literal per D007).
func rawURLPath(ctx context.Context, err error, req *http.Request) {
	report.Error(ctx, "request handling failed", err, map[string]string{"path": req.URL.Path}, "http.path") // want `ERC006: capture arg carries raw \*http.Request input \(r.URL.Path\)`
}

// raw r.Header.Get(...) under a different receiver name.
func rawHeaderGet(ctx context.Context, err error, httpReq *http.Request) {
	report.Warn(ctx, "trace lookup failed", err, map[string]string{"trace": httpReq.Header.Get("X-Trace")}, "http.hdr") // want `ERC006: capture arg carries raw \*http.Request input \(r.Header.Get\(\.\.\.\)\)`
}

// raw r.Body as a capture arg.
func rawBody(req *http.Request) {
	report.Panic("http", req.Body) // want `ERC006: capture arg carries raw \*http.Request input \(r.Body\)`
}

// same-shaped selector on an unrelated type: type-aware detection keeps it
// clean (cfg is not *http.Request), and the msg is a literal.
func cleanNonRequestSelector(ctx context.Context, err error) {
	var cfg struct{ URL struct{ Path string } }
	report.Error(ctx, "config load failed", err, map[string]string{"path": cfg.URL.Path}, "cfg.path")
}

// ---- msg-static (D007: Error / Warn / Notify) ----

// Error with a non-literal msg: dynamic data belongs in cause and tags.
func errorDynamicMsg(ctx context.Context, err error, msg string) {
	report.Error(ctx, msg, err, nil, "auth.creds") // want `ERC006: msg must be a static string literal`
}

// ---- notify: validated like a capture (D007 msg + D008 dedupKey), never one ----

// clean notify: static msg and a well-formed literal dedupKey.
func cleanNotify(ctx context.Context, err error) {
	report.Notify(ctx, "connection lost", err, nil, "net.offline")
}

// notify dedupKey is a non-literal variable (D008 - notify carries the 5-arg shape).
func notifyDedupNotLiteral(ctx context.Context, err error, dk string) {
	report.Notify(ctx, "connection lost", err, nil, dk) // want `ERC006: dedupKey must be a string literal`
}

// notify with a non-literal msg (D007).
func notifyDynamicMsg(ctx context.Context, err error, msg string) {
	report.Notify(ctx, msg, err, nil, "net.offline") // want `ERC006: msg must be a static string literal`
}

// notify wrong arity: dedupKey missing (D008, same 5-arg shape as a capture).
func notifyArityWrong(ctx context.Context, err error) {
	report.Notify(ctx, "connection lost", err, nil) // want `ERC006: capture call must pass 5 args`
}

// ---- quiet: dedupKey validated (D008), msg exempt (D007) ----

// quiet with a dynamic msg is clean - quiet is telemetry-only, so msg-static
// does not apply; the literal dedupKey still validates.
func quietDynamicMsgClean(ctx context.Context, err error, msg string) {
	report.Quiet(ctx, msg, err, nil, "cache.refresh")
}

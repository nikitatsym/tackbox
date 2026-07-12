package declared

import (
	"context"
	"net/http"
)

// myReport is declared in .tackbox-reporters (installed by the test). Its
// signature is unknown to ERC006, so dedupkey never applies to it; secret-arg
// and user-input still scrub its arguments.
func myReport(ctx context.Context, msg string, err error, key string) {}

// secret-arg fires on a declared sink.
func secretOnDeclared(ctx context.Context, err error, authToken string) {
	myReport(ctx, authToken, err, "note") // want `ERC006: capture arg names a secret \(authToken\)`
}

// user-input fires on a declared sink.
func userInputOnDeclared(ctx context.Context, err error, req *http.Request) {
	myReport(ctx, req.URL.Path, err, "note") // want `ERC006: capture arg carries raw \*http.Request input \(r.URL.Path\)`
}

// dedupkey does NOT apply to a declared sink: an ill-formed final arg and a
// literal that would fail the format regex are both clean, because the
// signature - and thus which arg is the dedupKey - is unknown.
func dedupIgnoredOnDeclared(ctx context.Context, err error) {
	myReport(ctx, "msg", err, "NOT.a.valid.Key")
}

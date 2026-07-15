package fingerprint

import (
	"context"

	"github.com/nikitatsym/tackbox/go/report"
)

// D-4: ERC006 skips *_test.go (EachFile), so a dynamic dedupKey that would fire
// in production draws no diagnostic here.
func testDynamicDedupExempt(ctx context.Context, err error, key string) {
	report.Error(ctx, "disk write failed", err, nil, key)
}

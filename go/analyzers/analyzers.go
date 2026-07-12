// Package analyzers exposes the full erclint ruleset as a slice of
// analysis.Analyzer values. Consumers wire it into multichecker or
// any other analysis driver.
package analyzers

import (
	"golang.org/x/tools/go/analysis"

	"github.com/nikitatsym/tackbox/go/analyzers/doublecapture"
	"github.com/nikitatsym/tackbox/go/analyzers/errcheck"
	"github.com/nikitatsym/tackbox/go/analyzers/fingerprint"
	"github.com/nikitatsym/tackbox/go/analyzers/parsenil"
	"github.com/nikitatsym/tackbox/go/analyzers/recoverswallow"
	"github.com/nikitatsym/tackbox/go/analyzers/returnnil"
	"github.com/nikitatsym/tackbox/go/analyzers/skiptest"
	"github.com/nikitatsym/tackbox/go/analyzers/terminal"
)

// All returns every native Go analyzer in the erclint ruleset. The order is
// stable and matches rule codes ERC001..ERC008.
func All() []*analysis.Analyzer {
	return []*analysis.Analyzer{
		errcheck.Analyzer,
		parsenil.Analyzer,
		terminal.Analyzer,
		returnnil.Analyzer,
		doublecapture.Analyzer,
		fingerprint.Analyzer,
		recoverswallow.Analyzer,
		skiptest.Analyzer,
	}
}

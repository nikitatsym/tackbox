// Package analyzers exposes the full erclint ruleset as a slice of
// analysis.Analyzer values. Consumers wire it into multichecker or
// any other analysis driver.
package analyzers

import (
	"golang.org/x/tools/go/analysis"

	"github.com/nikitatsym/tackbox/go/analyzers/doublecapture"
	"github.com/nikitatsym/tackbox/go/analyzers/errcheck"
	"github.com/nikitatsym/tackbox/go/analyzers/parsenil"
	"github.com/nikitatsym/tackbox/go/analyzers/returnnil"
	"github.com/nikitatsym/tackbox/go/analyzers/terminal"
)

// All returns every native Go analyzer in the erclint ruleset. The
// order is stable and matches rule codes ERC001..ERC005. ERC006
// (fingerprint) is enforced by opengrep, see python/erclint_opengrep.
func All() []*analysis.Analyzer {
	return []*analysis.Analyzer{
		errcheck.Analyzer,
		parsenil.Analyzer,
		terminal.Analyzer,
		returnnil.Analyzer,
		doublecapture.Analyzer,
	}
}

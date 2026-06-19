package parsenil_test

import (
	"testing"

	"golang.org/x/tools/go/analysis/analysistest"

	"github.com/nikitatsym/tackbox/go/analyzers/parsenil"
)

func TestAnalyzer(t *testing.T) {
	analysistest.Run(t, analysistest.TestData(), parsenil.Analyzer, "parsenil")
}

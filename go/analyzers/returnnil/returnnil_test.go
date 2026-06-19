package returnnil_test

import (
	"testing"

	"golang.org/x/tools/go/analysis/analysistest"

	"github.com/nikitatsym/tackbox/go/analyzers/returnnil"
)

func TestAnalyzer(t *testing.T) {
	analysistest.Run(t, analysistest.TestData(), returnnil.Analyzer, "returnnil")
}

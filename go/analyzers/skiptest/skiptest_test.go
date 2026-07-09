package skiptest_test

import (
	"testing"

	"golang.org/x/tools/go/analysis/analysistest"

	"github.com/nikitatsym/tackbox/go/analyzers/skiptest"
)

func TestAnalyzer(t *testing.T) {
	analysistest.Run(t, analysistest.TestData(), skiptest.Analyzer, "skiptest")
}

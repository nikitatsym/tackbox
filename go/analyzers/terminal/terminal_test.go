package terminal_test

import (
	"testing"

	"golang.org/x/tools/go/analysis/analysistest"

	"github.com/nikitatsym/tackbox/go/analyzers/terminal"
)

func TestAnalyzer(t *testing.T) {
	analysistest.Run(t, analysistest.TestData(), terminal.Analyzer, "terminal")
}

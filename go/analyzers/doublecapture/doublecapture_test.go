package doublecapture_test

import (
	"testing"

	"golang.org/x/tools/go/analysis/analysistest"

	"github.com/nikitatsym/tackbox/go/analyzers/doublecapture"
)

func TestAnalyzer(t *testing.T) {
	analysistest.Run(t, analysistest.TestData(), doublecapture.Analyzer, "doublecapture")
}

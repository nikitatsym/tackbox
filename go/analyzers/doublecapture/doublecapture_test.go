package doublecapture_test

import (
	"testing"

	"golang.org/x/tools/go/analysis/analysistest"

	"github.com/nikitatsym/tackbox/go/analyzers/doublecapture"
	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

func TestAnalyzer(t *testing.T) {
	astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), doublecapture.Analyzer, "doublecapture")
}

func TestDeclaredReporters(t *testing.T) {
	astutil.SetDeclaredReporters([]astutil.DeclaredReporter{
		{PkgPath: "declared", Name: "myReport"},
		{PkgPath: "declared", Name: "myErrReport"},
	})
	defer astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), doublecapture.Analyzer, "declared")
}

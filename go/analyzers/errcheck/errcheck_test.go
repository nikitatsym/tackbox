package errcheck_test

import (
	"testing"

	"golang.org/x/tools/go/analysis/analysistest"

	"github.com/nikitatsym/tackbox/go/analyzers/errcheck"
	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

func TestAnalyzer(t *testing.T) {
	astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), errcheck.Analyzer, "errcheck")
}

func TestDeclaredReporters(t *testing.T) {
	astutil.SetDeclaredReporters([]astutil.DeclaredReporter{
		{PkgPath: "declared", Name: "myReport"},
		{PkgPath: "declared", Name: "myDie"},
	})
	defer astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), errcheck.Analyzer, "declared")
}

func TestUsageSinkNotCapture(t *testing.T) {
	astutil.SetDeclaredReporters([]astutil.DeclaredReporter{{PkgPath: "usagesink", Name: "usage", Usage: true}})
	defer astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), errcheck.Analyzer, "usagesink")
}

package fingerprint_test

import (
	"testing"

	"golang.org/x/tools/go/analysis/analysistest"

	"github.com/nikitatsym/tackbox/go/analyzers/fingerprint"
	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

func TestAnalyzer(t *testing.T) {
	astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), fingerprint.Analyzer, "fingerprint")
}

func TestDeclaredReporters(t *testing.T) {
	astutil.SetDeclaredReporters([]astutil.DeclaredReporter{
		{PkgPath: "declared", Name: "myReport"},
	})
	defer astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), fingerprint.Analyzer, "declared")
}

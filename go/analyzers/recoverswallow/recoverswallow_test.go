package recoverswallow_test

import (
	"testing"

	"golang.org/x/tools/go/analysis/analysistest"

	"github.com/nikitatsym/tackbox/go/analyzers/recoverswallow"
	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

func TestAnalyzer(t *testing.T) {
	astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), recoverswallow.Analyzer, "recoverswallow")
}

func TestDeclaredReporters(t *testing.T) {
	astutil.SetDeclaredReporters([]astutil.DeclaredReporter{{PkgPath: "declared", Name: "myPanic"}})
	defer astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), recoverswallow.Analyzer, "declared")
}

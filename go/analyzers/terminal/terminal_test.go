package terminal_test

import (
	"testing"

	"golang.org/x/tools/go/analysis/analysistest"

	"github.com/nikitatsym/tackbox/go/analyzers/terminal"
	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

func TestAnalyzer(t *testing.T) {
	astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), terminal.Analyzer, "terminal")
}

func TestDeclaredReporters(t *testing.T) {
	astutil.SetDeclaredReporters([]astutil.DeclaredReporter{{PkgPath: "declared", Name: "myDie"}})
	defer astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), terminal.Analyzer, "declared")
}

func TestUsageSinks(t *testing.T) {
	astutil.SetDeclaredReporters([]astutil.DeclaredReporter{{PkgPath: "usagesink", Name: "usage", Usage: true}})
	defer astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), terminal.Analyzer, "usagesink")
}

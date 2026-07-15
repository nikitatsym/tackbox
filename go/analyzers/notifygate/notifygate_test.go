package notifygate_test

import (
	"testing"

	"golang.org/x/tools/go/analysis/analysistest"

	"github.com/nikitatsym/tackbox/go/analyzers/notifygate"
	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

func TestAnalyzer(t *testing.T) {
	astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), notifygate.Analyzer, "notifygate")
}

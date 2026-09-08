package errcheck_test

import (
	"strings"
	"testing"

	"golang.org/x/tools/go/analysis"

	"golang.org/x/tools/go/analysis/analysistest"

	"github.com/nikitatsym/tackbox/go/analyzers/errcheck"
	"github.com/nikitatsym/tackbox/go/internal/astutil"
	"github.com/nikitatsym/tackbox/go/internal/markers"
)

func TestAnalyzer(t *testing.T) {
	astutil.SetDeclaredReporters(nil)
	suppressed := map[int]string{}
	markers.Suppressed = func(pass *analysis.Pass, diagnostic analysis.Diagnostic, marker markers.Marker) {
		suppressed[pass.Fset.Position(marker.Pos).Line] = diagnostic.Message
	}
	defer func() { markers.Suppressed = nil }()
	analysistest.Run(t, analysistest.TestData(), errcheck.Analyzer, "errcheck")
	for _, line := range []int{115, 126} {
		if !strings.Contains(suppressed[line], "ERC001: err-branch must propagate") {
			t.Fatalf("marker at %d lost its suppressed rule: %v", line, suppressed)
		}
	}
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
	astutil.SetDeclaredReporters([]astutil.DeclaredReporter{
		{PkgPath: "usagesink", Name: "usage", Usage: true},
		{PkgPath: "usagesink", Name: "die", Usage: true},
	})
	defer astutil.SetDeclaredReporters(nil)
	analysistest.Run(t, analysistest.TestData(), errcheck.Analyzer, "usagesink")
}

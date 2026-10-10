package unusedparams_test

import (
	"go/token"
	"go/types"
	"strings"
	"testing"

	"golang.org/x/tools/go/analysis"
	"golang.org/x/tools/go/analysis/analysistest"

	"github.com/nikitatsym/tackbox/go/analyzers/unusedparams"
)

func TestAnalyzer(t *testing.T) {
	analysistest.Run(t, analysistest.TestData(), unusedparams.Analyzer, "params", "generated", "buildvariants")
}

func TestExcludedVariantParseError(t *testing.T) {
	pass := &analysis.Pass{
		Fset: token.NewFileSet(),
		Pkg: types.NewPackage("fixture", "fixture"),
		IgnoredFiles: []string{"broken_windows.go"},
		ReadFile: func(string) ([]byte, error) {
			return []byte("package fixture\nfunc broken("), nil
		},
	}
	_, err := unusedparams.Analyzer.Run(pass)
	if err == nil || !strings.Contains(err.Error(), "parse build-excluded Go file broken_windows.go") {
		t.Fatalf("want contextual parse failure, got %v", err)
	}
}

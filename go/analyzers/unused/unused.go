// Package unused exposes staticcheck's U1000 reachability check as ERC010.
package unused

import (
	"go/token"
	"strings"

	"golang.org/x/tools/go/analysis"
	"honnef.co/go/tools/analysis/facts/directives"
	"honnef.co/go/tools/analysis/facts/generated"
	"honnef.co/go/tools/analysis/lint"
	staticunused "honnef.co/go/tools/unused"
)

var Analyzer = &analysis.Analyzer{
	Name:     "unused",
	Doc:      "ERC010: unused unexported declarations (staticcheck U1000)",
	Requires: []*analysis.Analyzer{generated.Analyzer},
	Run:      run,
}

func run(pass *analysis.Pass) (interface{}, error) {
	upstreamPass := *pass
	// U1000 directives would introduce an unapproved suppression path.
	upstreamPass.ResultOf = map[*analysis.Analyzer]interface{}{
		directives.Analyzer: []lint.Directive(nil),
		generated.Analyzer:  pass.ResultOf[generated.Analyzer],
	}
	result, err := staticunused.Analyzer.Analyzer.Run(&upstreamPass)
	if err != nil {
		return nil, err
	}
	objects := result.(staticunused.Result).Unused
	if len(objects) == 0 {
		return nil, nil
	}
	files := make(map[string]*token.File, len(pass.Files))
	withTests := false
	for _, file := range pass.Files {
		tokenFile := pass.Fset.File(file.Pos())
		files[tokenFile.Name()] = tokenFile
		withTests = withTests || strings.HasSuffix(tokenFile.Name(), "_test.go")
	}
	for _, object := range objects {
		file := files[object.Position.Filename]
		// The normal package pass owns production findings, even for test-only uses.
		if withTests && !strings.HasSuffix(object.Position.Filename, "_test.go") {
			continue
		}
		pass.Reportf(file.Pos(object.Position.Offset),
			"ERC010: %s %s is unused (U1000)", object.Kind, object.Name)
	}
	return nil, nil
}

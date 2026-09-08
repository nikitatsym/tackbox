package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"sync"

	"golang.org/x/tools/go/analysis"
	"golang.org/x/tools/go/analysis/multichecker"

	"github.com/nikitatsym/tackbox/go/analyzers"
	"github.com/nikitatsym/tackbox/go/internal/astutil"
	"github.com/nikitatsym/tackbox/go/internal/markers"
	"github.com/nikitatsym/tackbox/go/internal/reporters"
	"github.com/nikitatsym/tackbox/go/internal/wrapcli"
	"github.com/nikitatsym/tackbox/go/report"
)

// version is injected at build time via -ldflags "-X main.version=...".
var version = "dev"

const reportersFlag = "--reporters="
const usageSinksFlag = "--usage-sinks="
const pathsFromFlag = "--paths-from"

func main() {
	var spec, usageSpec string
	var rest []string
	args := os.Args[1:]
	for i := 0; i < len(args); i++ {
		arg := args[i]
		switch {
		case arg == "--version" || arg == "-version":
			fmt.Printf("erclint %s\n", version)
			return
		case strings.HasPrefix(arg, reportersFlag):
			spec = arg[len(reportersFlag):]
		case strings.HasPrefix(arg, usageSinksFlag):
			usageSpec = arg[len(usageSinksFlag):]
		case arg == pathsFromFlag:
			if i+1 >= len(args) {
				report.Error(context.Background(), "read --paths-from",
					fmt.Errorf("%s requires a file argument", pathsFromFlag), nil, "erclint.pathsfrom")
				os.Exit(2)
			}
			i++
			// The package patterns ride a list-file instead of positional argv
			// (ARG_MAX safety); inject them where the positional args went.
			paths, err := wrapcli.ReadPathList(args[i])
			if err != nil {
				report.Error(context.Background(), "read --paths-from", err, nil, "erclint.pathsfrom")
				os.Exit(2)
			}
			rest = append(rest, paths...)
		default:
			rest = append(rest, arg)
		}
	}
	if spec != "" || usageSpec != "" {
		decls, err := reporters.Resolve(spec)
		if err == nil {
			var usage []astutil.DeclaredReporter
			usage, err = reporters.Resolve(usageSpec)
			for i := range usage {
				usage[i].Usage = true
			}
			decls = append(decls, usage...)
		}
		if err != nil {
			report.Error(context.Background(), "resolve .tackbox/reporters", err, nil, "erclint.reporters")
			os.Exit(2)
		}
		astutil.SetDeclaredReporters(decls)
	}
	for _, arg := range rest {
		switch arg {
		case "-json", "--json", "-json=true", "--json=true":
			markers.Suppressed = emitSuppressed
		case "-json=false", "--json=false":
			markers.Suppressed = nil
		}
	}
	os.Args = append([]string{os.Args[0]}, rest...)
	multichecker.Main(analyzers.All()...)
}

var outputMu sync.Mutex

func emitSuppressed(pass *analysis.Pass, diagnostic analysis.Diagnostic, marker markers.Marker) {
	finding := map[string]interface{}{
		"posn":        pass.Fset.Position(diagnostic.Pos).String(),
		"message":     diagnostic.Message,
		"suppressed":  true,
		"marker_kind": marker.Kind.String(),
		"marker_line": pass.Fset.Position(marker.Pos).Line,
	}
	outputMu.Lock()
	defer outputMu.Unlock()
	if err := json.NewEncoder(os.Stdout).Encode(map[string]interface{}{
		pass.Pkg.Path(): map[string]interface{}{pass.Analyzer.Name: []interface{}{finding}},
	}); err != nil {
		report.Error(context.Background(), "write suppressed finding", err, nil, "erclint.output")
		os.Exit(2)
	}
}

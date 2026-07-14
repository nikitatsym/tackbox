package main

import (
	"context"
	"fmt"
	"os"
	"strings"

	"golang.org/x/tools/go/analysis/multichecker"

	"github.com/nikitatsym/tackbox/go/analyzers"
	"github.com/nikitatsym/tackbox/go/internal/astutil"
	"github.com/nikitatsym/tackbox/go/internal/reporters"
	"github.com/nikitatsym/tackbox/go/report"
)

// version is injected at build time via -ldflags "-X main.version=...".
var version = "dev"

const reportersFlag = "--reporters="
const usageSinksFlag = "--usage-sinks="

func main() {
	var spec, usageSpec string
	var rest []string
	for _, arg := range os.Args[1:] {
		if arg == "--version" || arg == "-version" {
			fmt.Printf("erclint %s\n", version)
			return
		}
		if strings.HasPrefix(arg, reportersFlag) {
			spec = arg[len(reportersFlag):]
			continue
		}
		if strings.HasPrefix(arg, usageSinksFlag) {
			usageSpec = arg[len(usageSinksFlag):]
			continue
		}
		rest = append(rest, arg)
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
			report.Error(context.Background(), "resolve .tackbox-reporters", err, nil, "erclint.reporters")
			os.Exit(2)
		}
		astutil.SetDeclaredReporters(decls)
	}
	os.Args = append([]string{os.Args[0]}, rest...)
	multichecker.Main(analyzers.All()...)
}

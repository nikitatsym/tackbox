package main

import (
	"fmt"
	"os"

	"golang.org/x/tools/go/analysis/multichecker"

	"github.com/nikitatsym/tackbox/go/analyzers"
)

// version is injected at build time via -ldflags "-X main.version=...".
var version = "dev"

func main() {
	for _, arg := range os.Args[1:] {
		if arg == "--version" || arg == "-version" {
			fmt.Printf("erclint %s\n", version)
			return
		}
	}
	multichecker.Main(analyzers.All()...)
}

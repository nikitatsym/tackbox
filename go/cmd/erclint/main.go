package main

import (
	"golang.org/x/tools/go/analysis/multichecker"

	"github.com/nikitatsym/tackbox/go/analyzers"
)

func main() {
	multichecker.Main(analyzers.All()...)
}

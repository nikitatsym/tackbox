package usagesink

import (
	"fmt"
	"os"
)

// usage is declared `[usage]` (installed by the test).
func usage(msg string) {
	fmt.Fprintln(os.Stderr, msg)
	os.Exit(2)
}

func doThing() (int, error) { return 0, nil }

var mode = ""

func okTopLevel() {
	if len(os.Args) < 2 {
		usage("usage: tool <cmd>")
	}
}

// the exemption keys on context, not argument shape.
func okDynamicArg() {
	if mode != "a" && mode != "b" {
		usage("unknown mode: " + mode)
	}
}

func violationErrBranch() {
	_, err := doThing()
	if err != nil {
		usage("bad input") // want `ERC003:.*usage.*failure path`
	}
}

// carrying the error does not legalize the wrong sink.
func violationErrBranchCarriesErr() {
	_, err := doThing()
	if err != nil {
		usage("bad input: " + err.Error()) // want `ERC003:.*usage.*failure path`
	}
}

func okMarkerInErrBranch() {
	_, err := doThing()
	if err != nil {
		// no-report: exit code is the contract here, message is the report
		usage("bad input")
	}
}

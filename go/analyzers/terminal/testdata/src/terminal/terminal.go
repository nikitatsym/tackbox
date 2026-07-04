package terminal

import (
	"log"
	"os"

	"github.com/nikitatsym/tackbox/go/report"
)

func die(v any) {}

func doThing() (int, error) { return 0, nil }

var bootErr = error(nil)

func okCapture() {
	report.SentryErr("boot", "config broken", bootErr, nil, "boot.fatal")
	log.Fatal("config broken")
}

func okMarker() {
	// no-report: bootstrap-only, no Sentry stack yet
	log.Fatal("standalone boot failure")
}

func okExitMarker() {
	// no-report: normal exit
	os.Exit(0)
}

// option (b): the error flows into the terminal call - reported death, clean.
func okFatalCarriesErr() {
	_, err := doThing()
	if err != nil {
		log.Fatal(err)
	}
}

func okFatalfCarriesErr() {
	_, err := doThing()
	if err != nil {
		log.Fatalf("boot failed: %v", err)
	}
}

// die carrying the error - reported death, clean.
func okDieCarriesErr() {
	_, err := doThing()
	if err != nil {
		die(err)
	}
}

func violationFatal() {
	log.Fatal("config broken") // want `ERC003:.*log.Fatal.*preceded`
}

func violationExit() {
	os.Exit(1) // want `ERC003:.*os.Exit.*preceded`
}

func violationDie() {
	die("config broken") // want `ERC003:.*die.*preceded`
}

// static message while the error is live in scope - the error is dropped.
func violationStaticMsgWithErr() {
	_, err := doThing()
	if err != nil {
		log.Fatal("boot failed") // want `ERC003:.*log.Fatal.*preceded`
	}
}

// os.Exit in an err-branch, error not passed - silent exit.
func violationExitInErrBranch() {
	_, err := doThing()
	if err != nil {
		os.Exit(1) // want `ERC003:.*os.Exit.*preceded`
	}
}

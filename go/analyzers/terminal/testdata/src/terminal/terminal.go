package terminal

import (
	"log"
	"os"

	"github.com/nikitatsym/tackbox/go/report"
)

func die(msg string) {}

var bootErr = error(nil)

func okCapture() {
	report.SentryErr("boot", "config broken", bootErr, nil, "boot.fatal")
	log.Fatal("config broken")
}

func okMarker() {
	// no-sentry: bootstrap-only, no Sentry stack yet
	log.Fatal("standalone boot failure")
}

func okExitMarker() {
	// no-sentry: normal exit
	os.Exit(0)
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

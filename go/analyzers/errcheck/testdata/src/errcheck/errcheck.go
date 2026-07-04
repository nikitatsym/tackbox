package errcheck

import (
	"errors"
	"log"
	"os"

	"github.com/nikitatsym/tackbox/go/report"
)

// local helper sharing a former capture name - no longer a capture.
func sentryErr(area, msg string, err error, tags map[string]string, key string) {}

// die is in the hardcoded printing-terminal set alongside log.Fatal*.
func die(v any) {}

func okPropagate() error {
	err := errors.New("x")
	if err != nil {
		return err
	}
	return errors.New("noop")
}

func okCaptureTier1() error {
	err := errors.New("x")
	if err != nil {
		report.SentryErr("auth", "bad creds", err, nil, "auth.creds")
		return errors.New("noop")
	}
	return errors.New("noop")
}

func okMarker() error {
	err := errors.New("x")
	// no-sentry: caller already wraps and captures
	if err != nil {
		return errors.New("wrap")
	}
	return errors.New("noop")
}

// marker on the first line of the block above the branch (a continuation
// comment sits directly above the branch) still suppresses.
func okMarkerBlockAbove() error {
	err := errors.New("x")
	// no-sentry: caller already wraps and captures this at the boundary,
	// a reason long enough that splitting it across lines is the point
	if err != nil {
		return errors.New("wrap")
	}
	return errors.New("noop")
}

// a blank line breaks the adjacent block: the marker no longer applies.
func markerAcrossBlankLineFires() error {
	err := errors.New("x")
	// no-sentry: not adjacent

	if err != nil { // want `ERC001:.*err=err`
		return errors.New("wrap")
	}
	return errors.New("noop")
}

func okPanic() error {
	err := errors.New("x")
	if err != nil {
		panic(err)
	}
	return errors.New("noop")
}

// bare local sentryErr does not capture: name-trust is dead.
func bareLocalNotCapture() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		sentryErr("auth", "bad creds", err, nil, "auth.creds")
	}
	return errors.New("noop")
}

// report.Flush is in the package but is not a capture export.
func flushNotCapture() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		report.Flush()
	}
	return errors.New("noop")
}

func violation() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		_ = "swallowed"
	}
	return errors.New("noop")
}

// reported death: a printing terminal carrying the checked error is a handled
// branch - log.Fatal never returns and prints the error.
func okFatalCarriesErr() error {
	err := errors.New("x")
	if err != nil {
		log.Fatal(err)
	}
	return errors.New("noop")
}

func okFatalfCarriesErr() error {
	err := errors.New("x")
	if err != nil {
		log.Fatalf("boot failed: %v", err)
	}
	return errors.New("noop")
}

// die is in the printing-terminal set; carrying the error is a reported death.
func okDieCarriesErr() error {
	err := errors.New("x")
	if err != nil {
		die(err)
	}
	return errors.New("noop")
}

// static message drops the live error - not a reported death.
func fatalStaticMsgFires() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		log.Fatal("boot failed")
	}
	return errors.New("noop")
}

// log.Printf is not terminal: the branch falls through, the error is unhandled.
func printfNotTerminalFires() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		log.Printf("failed: %v", err)
	}
	return errors.New("noop")
}

// os.Exit is excluded from printing terminals: it does not print its argument,
// so name-based ArgFlows must not accept os.Exit(len(err.Error())) as a report.
func exitCarryingErrFires() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		os.Exit(len(err.Error()))
	}
	return errors.New("noop")
}

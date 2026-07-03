package errcheck

import (
	"errors"

	"github.com/nikitatsym/tackbox/go/report"
)

// local helper sharing a former capture name - no longer a capture.
func sentryErr(area, msg string, err error, tags map[string]string, key string) {}

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

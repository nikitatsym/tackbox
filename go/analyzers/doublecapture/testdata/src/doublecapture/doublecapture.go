package doublecapture

import (
	"errors"
	"fmt"

	"github.com/nikitatsym/tackbox/go/report"
)

func okCaptureOnly() error {
	err := errors.New("x")
	if err != nil {
		report.SentryErr("auth", "bad creds", err, nil, "auth.creds")
		return errors.New("wrap")
	}
	return errors.New("noop")
}

func okPropagateOnly() error {
	err := errors.New("x")
	if err != nil {
		return err
	}
	return errors.New("noop")
}

// error-capture + return err -> ERC005.
func violationBoth() error {
	err := errors.New("x")
	if err != nil { // want `ERC005:.*err=err`
		report.SentryErr("auth", "bad creds", err, nil, "auth.creds")
		return err
	}
	return errors.New("noop")
}

// panic-capture is terminal, excluded from ERC005: Panic + return err is ok.
func okPanicCaptureAndReturn() error {
	err := errors.New("x")
	if err != nil {
		report.Panic("boot", err)
		return err
	}
	return errors.New("noop")
}

// stringified re-report: the returned error still reaches the upstream
// handler, so capture + return is a double even with the chain broken.
func stringifiedReturnFires() error {
	err := errors.New("x")
	if err != nil { // want `ERC005:.*err=err`
		report.SentryErr("auth", "bad creds", err, nil, "auth.creds")
		return fmt.Errorf("ctx: %v", err)
	}
	return errors.New("noop")
}

func wrapCode(cause error) (int, error) { return 0, cause }

// a tuple-returning wrap after the capture: the error component reaches the
// upstream handler - still a double.
func tupleReturnFires() (int, error) {
	err := errors.New("x")
	if err != nil { // want `ERC005:.*err=err`
		report.SentryErr("auth", "bad creds", err, nil, "auth.creds")
		return wrapCode(err)
	}
	return 1, nil
}

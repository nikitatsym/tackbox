package doublecapture

import (
	"errors"

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

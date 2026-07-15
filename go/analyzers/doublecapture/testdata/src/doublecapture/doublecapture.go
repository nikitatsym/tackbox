package doublecapture

import (
	"errors"
	"fmt"

	"github.com/nikitatsym/tackbox/go/report"
)

func okCaptureOnly() error {
	err := errors.New("x")
	if err != nil {
		report.Error("auth", "bad creds", err, nil, "auth.creds")
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
		report.Error("auth", "bad creds", err, nil, "auth.creds")
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
		report.Error("auth", "bad creds", err, nil, "auth.creds")
		return fmt.Errorf("ctx: %v", err)
	}
	return errors.New("noop")
}

func offline(err error) bool { return err != nil }

// double-lane (D006): a capture and a notify on one path both reach the user -
// error/warn already show the user, so the paired notify double-shows.
func doubleLaneFires() error {
	err := errors.New("x")
	if err != nil { // want `ERC005:.*captures and notifies`
		report.Warn("net", "server error", err, nil, "net.fail")
		report.Notify("net", "something failed", err, nil, "net.notice")
	}
	return errors.New("noop")
}

// notify in one if-leg, capture in the exclusive else-leg: different paths, no
// double-lane (the canonical offline-vs-server-error split).
func okExclusiveLegs() error {
	err := errors.New("x")
	if err != nil {
		if offline(err) {
			report.Notify("net", "connection lost", err, nil, "net.offline")
		} else {
			report.Warn("net", "server unreachable", err, nil, "net.unreachable")
		}
	}
	return errors.New("noop")
}

// notify then return on the guarded path, capture on the fall-through: the
// return separates them, so no single path both captures and notifies.
func okNotifyReturnThenCapture() error {
	err := errors.New("x")
	if err != nil {
		if offline(err) {
			report.Notify("net", "connection lost", err, nil, "net.offline")
			return errors.New("handled")
		}
		report.Warn("net", "server error", err, nil, "net.fail")
	}
	return errors.New("noop")
}

func wrapCode(cause error) (int, error) { return 0, cause }

// a tuple-returning wrap after the capture: the error component reaches the
// upstream handler - still a double.
func tupleReturnFires() (int, error) {
	err := errors.New("x")
	if err != nil { // want `ERC005:.*err=err`
		report.Error("auth", "bad creds", err, nil, "auth.creds")
		return wrapCode(err)
	}
	return 1, nil
}

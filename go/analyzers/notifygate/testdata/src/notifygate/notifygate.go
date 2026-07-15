package notifygate

import (
	"errors"

	"github.com/nikitatsym/tackbox/go/report"
)

func offline(err error) bool { return err != nil }

// unconditional notify is the sole handling of the whole err-branch: it routes
// every failure to the user lane and blinds telemetry (D006).
func unconditionalNotifyFires() error {
	err := errors.New("x")
	if err != nil {
		report.Notify("net", "connection lost", err, nil, "net.offline") // want `ERC009:.*err=err`
	}
	return errors.New("noop")
}

// notify under an additional condition is narrowed: the gate is satisfied, and
// the complement path stays covered by ERC001.
func okConditionalNotify() error {
	err := errors.New("x")
	if err != nil {
		if offline(err) {
			report.Notify("net", "connection lost", err, nil, "net.offline")
			return errors.New("handled")
		}
		report.Error("net", "server error", err, nil, "net.fail")
	}
	return errors.New("noop")
}

// notify under a switch case is narrowed too.
func okSwitchNotify(code int) error {
	err := errors.New("x")
	if err != nil {
		switch code {
		case 503:
			report.Notify("net", "connection lost", err, nil, "net.offline")
		default:
			report.Error("net", "server error", err, nil, "net.fail")
		}
	}
	return errors.New("noop")
}

// notify the caught error does not reach is not terminating this failure path:
// ERC009 stays silent (ERC001 owns the swallow).
func okNotifyNoArgFlow() error {
	err := errors.New("x")
	if err != nil {
		report.Notify("net", "connection lost", errors.New("other"), nil, "net.offline")
	}
	return errors.New("noop")
}

// a no-report marker above the branch suppresses ERC009.
func okMarkerSuppresses() error {
	err := errors.New("x")
	// no-report: bootstrap notice, telemetry wired later in the boot sequence
	if err != nil {
		report.Notify("net", "connection lost", err, nil, "net.offline")
	}
	return errors.New("noop")
}

// a non-error guard is not an err-branch: the type gate keeps ERC009 silent.
func okNonErrorGuard(conn *int) {
	if conn != nil {
		report.Notify("net", "connection lost", errors.New("x"), nil, "net.offline")
	}
}

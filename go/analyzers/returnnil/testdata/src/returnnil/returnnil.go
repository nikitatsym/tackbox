package returnnil

import "errors"

func okMarker() *int {
	// nil-return: empty result is meaningful here
	return nil
}

func okWiderSignature() (*int, error) {
	return nil, errors.New("missing")
}

func okErrorType() error {
	return nil
}

type probeErr struct{ msg string }

func (e *probeErr) Error() string { return e.msg }

// a concrete error result: nil is the no-error value, the canonical Go
// contract - not a hidden empty result. Swallows stay on ERC001.
func okConcreteErrorNil() *probeErr {
	return nil
}

func violationPtr() *int {
	return nil // want `ERC004:.*widen the signature`
}

func violationSlice() []string {
	return nil // want `ERC004:.*widen the signature`
}

func violationMap() map[string]int {
	return nil // want `ERC004:.*widen the signature`
}

// --- err-branch guard covered by a valid `// no-report:` marker (same gate ERC001 uses) ---

// the guard's no-report marker covers the bare return nil inside the err-branch.
func okErrGuardMarkerCoversReturnNil() *int {
	err := errors.New("x")
	// no-report: legacy nil sentinel while callers migrate off it
	if err != nil {
		return nil
	}
	v := 1
	return &v
}

func errGuardNoMarkerFires() *int {
	err := errors.New("x")
	if err != nil {
		return nil // want `ERC004:.*widen the signature`
	}
	v := 1
	return &v
}

// the no-report marker sits on an unrelated guard; it must not leak to a
// return nil outside that guard's body.
func markerOnUnrelatedGuardDoesNotLeak() *int {
	err := errors.New("x")
	// no-report: unrelated guard, does not cover the return below
	if err != nil {
		v := 1
		return &v
	}
	return nil // want `ERC004:.*widen the signature`
}

// empty-reason no-report is not a marker at all: markers.Index rejects it.
func emptyReasonMarkerStillFires() *int {
	err := errors.New("x")
	// no-report:
	if err != nil {
		return nil // want `ERC004:.*widen the signature`
	}
	v := 1
	return &v
}

// guard on a non-error identifier is not an err-branch: the marker has no
// handling site to attach to.
func nonErrorGuardMarkerStillFires(conn *int) *int {
	// no-report: conn nilness is not an error branch
	if conn != nil {
		return nil // want `ERC004:.*widen the signature`
	}
	v := 1
	return &v
}

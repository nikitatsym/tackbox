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
	return nil // want `ERC004:.*nil-return`
}

func violationSlice() []string {
	return nil // want `ERC004:.*nil-return`
}

func violationMap() map[string]int {
	return nil // want `ERC004:.*nil-return`
}

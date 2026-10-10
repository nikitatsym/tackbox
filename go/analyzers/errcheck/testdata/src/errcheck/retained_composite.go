package errcheck

import "errors"

type embeddedError interface{ error }
type pwErrorMsg struct{ err error }

func (p pwErrorMsg) Error() string { return p.err.Error() }

type carrierResult struct {
	Value any
	Err   error
}
type carrierResponse struct{ Err error }
type positionalCarrier struct{ Err error }
type embeddedCarrier struct{ Err embeddedError }
type stringCarrier struct{ Err string }
type anyCarrier struct{ Err any }

func returnedErrorMessage() error {
	err := errors.New("cause")
	if err != nil {
		return pwErrorMsg{err: err}
	}
	return nil
}
func returnedValueCarrier() carrierResult {
	err := errors.New("cause")
	if err != nil {
		return carrierResult{Value: nil, Err: err}
	}
	return carrierResult{}
}
func returnedPointerCarrier() *carrierResponse {
	err := errors.New("cause")
	if err != nil {
		return &carrierResponse{Err: err}
	}
	return nil
}
func returnedPositionalCarrier() positionalCarrier {
	err := errors.New("cause")
	if err != nil {
		return positionalCarrier{err}
	}
	return positionalCarrier{}
}
func returnedEmbeddedCarrier() embeddedCarrier {
	err := errors.New("cause")
	if err != nil {
		return embeddedCarrier{Err: err}
	}
	return embeddedCarrier{}
}
func returnedStringCarrier() stringCarrier {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		return stringCarrier{Err: err.Error()}
	}
	return stringCarrier{}
}
func returnedAnyCarrier() anyCarrier {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		return anyCarrier{Err: err}
	}
	return anyCarrier{}
}
func returnedDerivedCarrier() carrierResult {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		return carrierResult{Err: errors.New(err.Error())}
	}
	return carrierResult{}
}
func droppedCarrier() {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		built := carrierResult{Err: err}
		_ = built
	}
}
func returnedAnyErrorMessage() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		return anyErrorMsg{Err: err}
	}
	return nil
}

type anyErrorMsg struct{ Err any }

func (anyErrorMsg) Error() string { return "failure" }

func returnedCapturedErrorCell() error {
	err := errors.New("cause")
	defer func() { _ = err }()
	if err != nil {
		return &pwErrorMsg{err: err}
	}
	return nil
}

func overwrittenCapturedErrorCell() error {
	err := errors.New("cause")
	defer func() { _ = err }()
	if err != nil { // want `ERC001:.*err=err`
		err = errors.New("replacement")
		return &pwErrorMsg{err: err}
	}
	return nil
}

package errcheck

import (
	"errors"
	"fmt"
)

func convertedAnyCarrier() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		return error(anyErrorMsg{Err: err})
	}
	return nil
}
func keyedOtherErrorInCall(other error) error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		return fmt.Errorf("wrap: %w", &pwErrorMsg{err: other})
	}
	return nil
}
func selectedOtherErrorInCall(value pwErrorMsg) error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		return fmt.Errorf("wrap: %w", value.err)
	}
	return nil
}
func shadowedErrorInCall() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		err := errors.New("replacement")
		return fmt.Errorf("wrap: %w", err)
	}
	return nil
}
func (p *pwErrorMsg) reset() { p.err = nil }

type pointerHolder struct{ P *pwErrorMsg }

func mutatedByClosure() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		p := &pwErrorMsg{err: err}
		func() { p.err = nil }()
		return p
	}
	return nil
}
func capturedCellMutatedByClosure() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		func() { err = nil }()
		return &pwErrorMsg{err: err}
	}
	return nil
}
func mutatedByDefer() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		p := &pwErrorMsg{err: err}
		defer func() { p.err = nil }()
		return p
	}
	return nil
}
func mutatedByGo() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		p := &pwErrorMsg{err: err}
		go func() { p.err = nil }()
		return p
	}
	return nil
}
func mutatedByPointerMethod() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		p := &pwErrorMsg{err: err}
		p.reset()
		return p
	}
	return nil
}
func mutatedByStoredAlias() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		p := &pwErrorMsg{err: err}
		h := &pointerHolder{P: p}
		h.P.err = nil
		return p
	}
	return nil
}

var globalError error

func clearGlobalError() { globalError = nil }
func mutatedGlobalError() error {
	if globalError != nil { // want `ERC001:.*err=globalError`
		clearGlobalError()
		return &pwErrorMsg{err: globalError}
	}
	return nil
}
func gmuxNamedFieldOtherError(other error) error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		return &pwErrorMsg{err: other}
	}
	return nil
}
func overwrittenPointerCarrier() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		p := &pwErrorMsg{err: err}
		p.err = nil
		return p
	}
	return nil
}
func conditionalCompositeCarrier(flag bool) error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		if flag {
			return &pwErrorMsg{err: err}
		}
		return &pwErrorMsg{}
	}
	return nil
}
func varDeclaredCompositeCarrier() error {
	err := errors.New("cause")
	if err != nil {
		var p error = &pwErrorMsg{err: err}
		return p
	}
	return nil
}
func directJoinedCarrier(other error) error {
	err := errors.New("cause")
	if err != nil {
		return &pwErrorMsg{err: errors.Join(err, other)}
	}
	return nil
}
func multiWrappedCarrier(other error) error {
	err := errors.New("cause")
	if err != nil {
		return &pwErrorMsg{err: fmt.Errorf("%w; %w", other, err)}
	}
	return nil
}

type connectionState struct{ broken bool }
type clientState struct{ conn *connectionState }

func unrelatedPointerMutation(client *clientState) error {
	err := errors.New("cause")
	if err != nil {
		p := &pwErrorMsg{err: err}
		client.conn.broken = true
		return p
	}
	return nil
}
func unrelatedMutationWithCapturedCell(client *clientState) error {
	err := errors.New("cause")
	defer func() { _ = err }()
	if err != nil {
		client.conn.broken = true
		return &pwErrorMsg{err: err}
	}
	return nil
}

type pathFailure struct{ err error }

func (p *pathFailure) Error() string { return p.err.Error() }
func aliasDeclaredInsideBranch() error {
	err := errors.New("cause")
	if err != nil {
		var pe *pathFailure
		if errors.As(err, &pe) {
			return fmt.Errorf("path: %w", pe)
		}
		return err
	}
	return nil
}

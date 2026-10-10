package errcheck

import (
	"errors"
	"fmt"
	"os"
)

type outerCarrier struct{ Inner diagnostic }
type embeddedDiagnostic struct{ diagnostic }
type genericBox[T any] struct{ Err T }
type sliceCarrier struct{ Errs []error }
type concreteCarrier struct{ Err *os.PathError }

func trustedCarrier(value any) error         { return errors.New("trusted replacement") }
func (value anyErrorMsg) self() error        { return errors.New("trusted replacement") }
func (value pwErrorMsg) self() error         { return errors.New("trusted replacement") }
func trustedErrorArgument(value error) error { return errors.New("trusted replacement") }

func approvedExternalConstructor() diagnostic {
	err := errors.New("cause")
	if err != nil {
		return diagnostic{Detail: os.NewSyscallError("read", err)}
	}
	return diagnostic{}
}
func approvedNested() outerCarrier {
	err := errors.New("cause")
	if err != nil {
		return outerCarrier{Inner: diagnostic{Detail: err}}
	}
	return outerCarrier{}
}
func approvedEmbedded() embeddedDiagnostic {
	err := errors.New("cause")
	if err != nil {
		return embeddedDiagnostic{diagnostic{Detail: err}}
	}
	return embeddedDiagnostic{}
}
func approvedGeneric() genericBox[error] {
	err := errors.New("cause")
	if err != nil {
		return genericBox[error]{Err: err}
	}
	return genericBox[error]{}
}
func deniedGenericAny() genericBox[any] {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		return genericBox[any]{Err: err}
	}
	return genericBox[any]{}
}
func approvedErrorSlice() sliceCarrier {
	err := errors.New("cause")
	if err != nil {
		return sliceCarrier{Errs: []error{err}}
	}
	return sliceCarrier{}
}
func approvedConcrete(err *os.PathError) concreteCarrier {
	if err != nil {
		return concreteCarrier{Err: err}
	}
	return concreteCarrier{}
}
func approvedBoxed() any {
	err := errors.New("cause")
	if err != nil {
		return diagnostic{Detail: err}
	}
	return nil
}
func approvedAssignedBoxed() any {
	err := errors.New("cause")
	if err != nil {
		var value any = diagnostic{Detail: err}
		return value
	}
	return nil
}
func deniedBoxedAnyField() any {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		return anyCarrier{Err: err}
	}
	return nil
}
func approvedCarrierCall() error {
	err := errors.New("cause")
	if err != nil {
		return trustedCarrier(diagnostic{Detail: err})
	}
	return nil
}
func deniedCarrierCall() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		return trustedCarrier(anyCarrier{Err: err})
	}
	return nil
}
func deniedAssignedCarrierCall() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		value := anyCarrier{Err: err}
		return trustedCarrier(value)
	}
	return nil
}
func approvedReceiverCall() error {
	err := errors.New("cause")
	if err != nil {
		return pwErrorMsg{err: err}.self()
	}
	return nil
}
func deniedReceiverCall() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		return anyErrorMsg{Err: err}.self()
	}
	return nil
}
func approvedAsField() diagnostic {
	err := errors.New("cause")
	if err != nil {
		var pe *os.PathError
		if errors.As(err, &pe) {
			return diagnostic{Detail: pe}
		}
		return diagnostic{Detail: err}
	}
	return diagnostic{}
}
func deniedOverwrittenAsField() diagnostic {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		var pe *os.PathError
		if errors.As(err, &pe) {
			pe = nil
			return diagnostic{Detail: pe}
		}
		return diagnostic{Detail: err}
	}
	return diagnostic{}
}
func approvedIndexedWrap() error {
	err := errors.New("cause")
	if err != nil {
		return fmt.Errorf("%[2]w (%[1]s)", "ctx", err)
	}
	return nil
}
func approvedIndexedFieldWrap() diagnostic {
	err := errors.New("cause")
	if err != nil {
		return diagnostic{Detail: fmt.Errorf("%[2]w (%[1]s)", "ctx", err)}
	}
	return diagnostic{}
}
func deniedWrongWrappedOperand() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		return fmt.Errorf("%w: %v", errors.New("sentinel"), err)
	}
	return nil
}
func deniedAssignedWrongOperand() error {
	err := errors.New("cause")
	if err != nil { // want `ERC001:.*err=err`
		wrapped := fmt.Errorf("%[1]w: %[2]v", errors.New("sentinel"), err)
		return wrapped
	}
	return nil
}

func approvedCarrierThroughErrorArgument() error {
	err := errors.New("cause")
	if err != nil {
		carrier := pwErrorMsg{err: err}
		return trustedErrorArgument(carrier)
	}
	return nil
}

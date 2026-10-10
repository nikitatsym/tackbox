package errcheck

import (
	"errors"
	"fmt"
)

func wrappedDiagnostic(cause error) diagnostic {
	return diagnostic{Detail: fmt.Errorf("context: %w", cause)}
}
func retainedWrappedDiagnostic() diagnostic {
	err := errors.New("original")
	if err != nil {
		return wrappedDiagnostic(err)
	}
	return diagnostic{}
}
func unrelatedFormatArgument() diagnostic {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		return diagnostic{Detail: fmt.Errorf("%v %w", err, errors.New("other"))}
	}
	return diagnostic{}
}

func discardedExtraFormatArgument() diagnostic {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		return diagnostic{Detail: fmt.Errorf("%w", errors.New("other"), err)}
	}
	return diagnostic{}
}

func retainedContextWrappedCarrier() error {
	err := errors.New("original")
	if err != nil {
		return &pwErrorMsg{err: fmt.Errorf("context %s: %w", "name", err)}
	}
	return nil
}

func retainedJoinedWrappedCarrier(other error) error {
	err := errors.New("original")
	if err != nil {
		return &pwErrorMsg{err: fmt.Errorf("write record: %w", errors.Join(err, other))}
	}
	return nil
}

func retainedTrailingContextCarrier(other error) error {
	err := errors.New("original")
	if err != nil {
		return &pwErrorMsg{err: fmt.Errorf("failure %s: %w (other: %v)", "name", err, other)}
	}
	return nil
}

func retainedNestedWrapperCarrier() error {
	err := errors.New("original")
	if err != nil {
		return &pwErrorMsg{err: pwErrorMsg{err: fmt.Errorf("context %d: %w", 1, err)}}
	}
	return nil
}

func nonErrorWrappedCarrier() diagnostic {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		value := any(diagnostic{Detail: err})
		return diagnostic{Detail: fmt.Errorf("context: %w", value)}
	}
	return diagnostic{}
}

func nonErrorTopLevelWrap() error {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		return fmt.Errorf("ctx: %w", diagnostic{Detail: err})
	}
	return nil
}

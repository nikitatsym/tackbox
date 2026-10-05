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

package parsenil

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
)

type nestedResult struct{ Inner diagnostic }
type genericResult[T any] struct{ Err T }

func trustedParserCarrier(value any) error { return errors.New("trusted replacement") }

func parserExternalConstructor(data []byte) diagnostic {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return diagnostic{Failure: os.NewSyscallError("read", err)}
	}
	return diagnostic{}
}
func parserNested(data []byte) nestedResult {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return nestedResult{Inner: diagnostic{Failure: err}}
	}
	return nestedResult{}
}
func parserGeneric(data []byte) genericResult[error] {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return genericResult[error]{Err: err}
	}
	return genericResult[error]{}
}
func parserGenericAny(data []byte) genericResult[any] {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		return genericResult[any]{Err: err}
	}
	return genericResult[any]{}
}
func parserBoxed(data []byte) any {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		var result any = diagnostic{Failure: err}
		return result
	}
	return nil
}
func parserBoxedAnyField(data []byte) any {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		return anyCarrier{Err: err}
	}
	return nil
}
func parserCarrierUnderCall(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return trustedParserCarrier(diagnostic{Failure: err})
	}
	return nil
}
func parserAnyCarrierUnderCall(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		result := anyCarrier{Err: err}
		return trustedParserCarrier(result)
	}
	return nil
}
func parserAsField(data []byte) diagnostic {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		var pe *os.PathError
		if errors.As(err, &pe) {
			return diagnostic{Failure: pe}
		}
		return diagnostic{Failure: err}
	}
	return diagnostic{}
}
func parserAsOverwritten(data []byte) diagnostic {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		var pe *os.PathError
		if errors.As(err, &pe) {
			pe = nil
			return diagnostic{Failure: pe}
		}
		return diagnostic{Failure: err}
	}
	return diagnostic{}
}
func parserIndexedWrap(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return fmt.Errorf("%[2]w (%[1]s)", "ctx", err)
	}
	return nil
}
func parserIndexedField(data []byte) diagnostic {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return diagnostic{Failure: fmt.Errorf("%[2]w (%[1]s)", "ctx", err)}
	}
	return diagnostic{}
}
func parserWrongWrappedOperand(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		return fmt.Errorf("%w: %v", errors.New("sentinel"), err)
	}
	return nil
}
func parserAssignedWrongOperand(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		wrapped := fmt.Errorf("%[1]w: %[2]v", errors.New("sentinel"), err)
		return wrapped
	}
	return nil
}

type parserEmbedded struct{ diagnostic }
type parserErrorSlice struct{ Errors []error }
type parserConcrete struct{ Err *os.PathError }

func parserEmbeddedStructCarrier(data []byte) parserEmbedded {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return parserEmbedded{diagnostic{Failure: err}}
	}
	return parserEmbedded{}
}
func parserSliceCarrier(data []byte) parserErrorSlice {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return parserErrorSlice{Errors: []error{err}}
	}
	return parserErrorSlice{}
}
func parserConcreteCarrier(data []byte) parserConcrete {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		var pe *os.PathError
		if errors.As(err, &pe) {
			return parserConcrete{Err: pe}
		}
		return parserConcrete{Err: &os.PathError{Err: err}}
	}
	return parserConcrete{}
}
func parserAssignedTrustedCarrier(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		carrier := diagnostic{Failure: err}
		return trustedParserCarrier(carrier)
	}
	return nil
}
func parserAssignedAsCarrier(data []byte) any {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		var pe *os.PathError
		if errors.As(err, &pe) {
			carrier := diagnostic{Failure: pe}
			return any(carrier)
		}
		return diagnostic{Failure: err}
	}
	return nil
}

func parserNonErrorTopLevelWrap(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		return fmt.Errorf("ctx: %w", diagnostic{Failure: err})
	}
	return nil
}

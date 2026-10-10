package parsenil

import (
	"encoding/json"
	"errors"
	"fmt"
)

type embeddedError interface{ error }
type errorMsg struct{ err error }

func (m errorMsg) Error() string { return m.err.Error() }

type carrierResult struct {
	Value any
	Err   error
}
type carrierResponse struct{ Err error }
type positionalCarrier struct{ Err error }
type embeddedCarrier struct{ Err embeddedError }
type stringCarrier struct{ Err string }
type anyCarrier struct{ Err any }

func parserErrorMessage(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return errorMsg{err: err}
	}
	return nil
}
func parserValueCarrier(data []byte) carrierResult {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return carrierResult{Value: nil, Err: err}
	}
	return carrierResult{}
}
func parserPointerCarrier(data []byte) *carrierResponse {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return &carrierResponse{Err: err}
	}
	return nil
}
func parserPositionalCarrier(data []byte) positionalCarrier {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return positionalCarrier{err}
	}
	return positionalCarrier{}
}
func parserEmbeddedCarrier(data []byte) embeddedCarrier {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return embeddedCarrier{Err: err}
	}
	return embeddedCarrier{}
}
func parserStringCarrier(data []byte) stringCarrier {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		return stringCarrier{Err: err.Error()}
	}
	return stringCarrier{}
}
func parserAnyCarrier(data []byte) anyCarrier {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		return anyCarrier{Err: err}
	}
	return anyCarrier{}
}
func parserDerivedCarrier(data []byte) carrierResult {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		return carrierResult{Err: errors.New(err.Error())}
	}
	return carrierResult{}
}
func parserDroppedCarrier(data []byte) {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		built := carrierResult{Err: err}
		_ = built
	}
}
func parserAnyErrorMessage(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		return anyErrorMsg{Err: err}
	}
	return nil
}

type anyErrorMsg struct{ Err any }

func (anyErrorMsg) Error() string { return "failure" }

func parserWrappedCarrier(data []byte, other error) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return &errorMsg{err: fmt.Errorf("context %s: %w (other: %v)", "name", errors.Join(err, other), other)}
	}
	return nil
}

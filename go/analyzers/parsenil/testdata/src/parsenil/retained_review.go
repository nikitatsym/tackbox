package parsenil

import (
	"encoding/json"
	"errors"
	"fmt"
)

func parserConvertedAnyCarrier(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		return error(anyErrorMsg{Err: err})
	}
	return nil
}
func parserKeyOtherError(data []byte, other error) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		return fmt.Errorf("wrap: %w", &errorMsg{err: other})
	}
	return nil
}
func parserSelectorOtherError(data []byte, holder errorMsg) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		return fmt.Errorf("wrap: %w", holder.err)
	}
	return nil
}
func parserShadowedError(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		err := errors.New("replacement")
		return fmt.Errorf("wrap: %w", err)
	}
	return nil
}
func parserCapturedCell(data []byte) error {
	var value string
	var err error
	defer func() { _ = err }()
	err = json.Unmarshal(data, &value)
	if err != nil {
		return &errorMsg{err: err}
	}
	return nil
}
func parserCapturedCellMutated(data []byte) error {
	var value string
	err := json.Unmarshal(data, &value)
	if err != nil { // want `ERC002:.*err-branch`
		func() { err = nil }()
		return &errorMsg{err: err}
	}
	return nil
}
func parserVarDeclaredCarrier(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		var result error = &errorMsg{err: err}
		return result
	}
	return nil
}
func parserDirectJoinedCarrier(data []byte, other error) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return &errorMsg{err: errors.Join(err, other)}
	}
	return nil
}
func parserMultipleWrapping(data []byte, other error) error {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return &errorMsg{err: fmt.Errorf("%w; %w", other, err)}
	}
	return nil
}

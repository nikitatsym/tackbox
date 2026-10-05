package parsenil

import (
	"encoding/json"
	"errors"
)

type diagnostic struct{ Failure error }
type result struct{ Notices []diagnostic }

func notice(cause error) diagnostic     { return diagnostic{Failure: cause} }
func fakeNotice(cause error) diagnostic { _ = cause; return diagnostic{} }

func retainedParser(data []byte) []diagnostic {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return []diagnostic{{Failure: err}}
	}
	return nil
}
func retainedParserHelper(data []byte) diagnostic {
	var value string
	if err := json.Unmarshal(data, &value); err != nil {
		return notice(err)
	}
	return diagnostic{}
}
func retainedParserAppend(data []byte) result {
	item := result{}
	var value string
	err := json.Unmarshal(data, &value)
	if err != nil {
		item.Notices = append(item.Notices, diagnostic{Failure: err})
	}
	return item
}
func lostParser(data []byte) {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		_ = diagnostic{Failure: err}
	}
}
func fakeParserHelper(data []byte) diagnostic {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		return fakeNotice(err)
	}
	return diagnostic{}
}
func wrongParserError(data []byte) diagnostic {
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		return diagnostic{Failure: errors.New("wrong error")}
	}
	return diagnostic{}
}
func clearedParser(data []byte) diagnostic {
	item := diagnostic{}
	var value string
	if err := json.Unmarshal(data, &value); err != nil { // want `ERC002:.*err-branch`
		item.Failure = err
	}
	item.Failure = nil
	return item
}

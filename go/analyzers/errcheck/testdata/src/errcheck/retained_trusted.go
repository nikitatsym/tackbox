package errcheck

import (
	"errors"
	"fmt"
)

func trustedTextConstructor(label string, cause error) error {
	return fmt.Errorf("%s: %s", label, cause)
}
func approvedConditionalCarrierCall() error {
	err := errors.New("cause")
	if err != nil {
		if errors.Is(err, errors.ErrUnsupported) {
			return trustedTextConstructor("context", pwErrorMsg{err: err})
		}
		return trustedTextConstructor("context", err)
	}
	return nil
}

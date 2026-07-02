package pkga

import "errors"

func Violate() error {
	err := errors.New("x")
	if err != nil {
		_ = "swallowed"
	}
	return errors.New("noop")
}

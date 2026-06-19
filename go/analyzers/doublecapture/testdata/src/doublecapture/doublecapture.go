package doublecapture

import "errors"

func sentryErr(area, msg string, err error, tags map[string]string, key string) {}

func okCaptureOnly() error {
	err := errors.New("x")
	if err != nil {
		sentryErr("auth", "bad creds", err, nil, "auth.creds")
		return errors.New("wrap")
	}
	return errors.New("noop")
}

func okPropagateOnly() error {
	err := errors.New("x")
	if err != nil {
		return err
	}
	return errors.New("noop")
}

func violationBoth() error {
	err := errors.New("x")
	if err != nil { // want `ERC005:.*err=err`
		sentryErr("auth", "bad creds", err, nil, "auth.creds")
		return err
	}
	return errors.New("noop")
}

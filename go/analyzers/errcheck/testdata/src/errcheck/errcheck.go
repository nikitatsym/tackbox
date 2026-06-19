package errcheck

import "errors"

func sentryErr(area, msg string, err error, tags map[string]string, key string) {}

func okPropagate() error {
	err := errors.New("x")
	if err != nil {
		return err
	}
	return errors.New("noop")
}

func okCapture() error {
	err := errors.New("x")
	if err != nil {
		sentryErr("auth", "bad creds", err, nil, "auth.creds")
		return errors.New("noop")
	}
	return errors.New("noop")
}

func okMarker() error {
	err := errors.New("x")
	// no-sentry: caller already wraps and captures
	if err != nil {
		return errors.New("wrap")
	}
	return errors.New("noop")
}

func okPanic() error {
	err := errors.New("x")
	if err != nil {
		panic(err)
	}
	return errors.New("noop")
}

func violation() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		_ = "swallowed"
	}
	return errors.New("noop")
}

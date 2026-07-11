package usagesink

import "errors"

// usage is declared `[usage]` (installed by the test): never a capture.
func usage(msg string) {}

func usageSinkDoesNotCapture() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		usage("bad input: " + err.Error())
	}
	return errors.New("noop")
}

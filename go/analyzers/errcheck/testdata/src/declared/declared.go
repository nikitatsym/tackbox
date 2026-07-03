package declared

import "errors"

// myReport is declared in .tackbox-reporters (installed by the test). A call
// captures only when the caught error flows into its arguments.
func myReport(err error) {}

func okDeclaredCapture() error {
	err := errors.New("x")
	if err != nil {
		myReport(err)
	}
	return errors.New("noop")
}

func declaredNoArgFlowFires() error {
	err := errors.New("x")
	other := errors.New("y")
	if err != nil { // want `ERC001:.*err=err`
		myReport(other)
	}
	return errors.New("noop")
}

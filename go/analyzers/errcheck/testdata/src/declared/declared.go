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

type pathErr struct{ msg string }

func (e *pathErr) Error() string { return e.msg }

// capture through the errors.As alias is capture of the guarded error - the
// capture is unconditional, so every path through the branch is credited.
func okDeclaredCaptureViaAsAlias() error {
	err := errors.New("x")
	if err != nil {
		var pe *pathErr
		errors.As(err, &pe)
		myReport(pe)
	}
	return errors.New("noop")
}

// myDie is declared (installed by the test): the body is the trust boundary,
// reviewed at declaration time - analyzers do not look inside (B3c).
func myDie(err error) {
	if err != nil {
		_ = "reviewed at declaration, not analyzed"
	}
}

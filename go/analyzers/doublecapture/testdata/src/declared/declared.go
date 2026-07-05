package declared

import (
	"errors"
	"fmt"
	"os"
)

// myReport is declared in .tackbox-reporters (installed by the test); it
// prints the error and hands back a process exit code.
func myReport(err error) int {
	fmt.Fprintln(os.Stderr, err)
	return 3
}

// myErrReport is a declared sink that hands back an error for the caller.
func myErrReport(err error) error {
	fmt.Fprintln(os.Stderr, err)
	return err
}

// returning the sink's exit code is a single capture, not capture + return
// err: an int cannot carry the error upstream.
func okReturnSinkCode() int {
	err := errors.New("x")
	if err != nil {
		return myReport(err)
	}
	return 0
}

// the two-line form of the same site.
func okReturnSinkCodeTwoLine() int {
	err := errors.New("x")
	if err != nil {
		code := myReport(err)
		return code
	}
	return 0
}

// an error-returning sink in the return: the capture propagates an error the
// upstream handler will re-capture - the double ERC005 exists for.
func errSinkReturnFires() error {
	err := errors.New("x")
	if err != nil { // want `ERC005:.*err=err`
		return myErrReport(err)
	}
	return errors.New("noop")
}

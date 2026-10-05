package declared

import "errors"

type diagnostic struct{ Original error }

func emitDiagnostic(value diagnostic)   {}
func anonymousCapture(value diagnostic) {}

func emittedRetainedDiagnostic() {
	err := errors.New("original")
	if err != nil {
		emitDiagnostic(diagnostic{Original: err})
	}
}
func wrongEmittedDiagnostic() {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		emitDiagnostic(diagnostic{Original: errors.New("other")})
	}
}
func undeclaredEmission() {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		anonymousCapture(diagnostic{Original: err})
	}
}

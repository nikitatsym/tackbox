package errcheck

import (
	"errors"
	"fmt"
	"log"
	"os"

	"github.com/nikitatsym/tackbox/go/report"
)

// local helper sharing the capture name - no longer a capture (origin, not name).
func Error(area, msg string, err error, tags map[string]string, key string) {}

// die is in the hardcoded printing-terminal set alongside log.Fatal*.
func die(v any) {}

func okPropagate() error {
	err := errors.New("x")
	if err != nil {
		return err
	}
	return errors.New("noop")
}

func okCaptureTier1() error {
	err := errors.New("x")
	if err != nil {
		report.Error("auth", "bad creds", err, nil, "auth.creds")
		return errors.New("noop")
	}
	return errors.New("noop")
}

func okMarker() error {
	err := errors.New("x")
	// no-report: caller already wraps and captures
	if err != nil {
		return errors.New("wrap")
	}
	return errors.New("noop")
}

// marker on the first line of the block above the branch (a continuation
// comment sits directly above the branch) still suppresses.
func okMarkerBlockAbove() error {
	err := errors.New("x")
	// no-report: caller already wraps and captures this at the boundary,
	// a reason long enough that splitting it across lines is the point
	if err != nil {
		return errors.New("wrap")
	}
	return errors.New("noop")
}

// a blank line breaks the adjacent block: the marker no longer applies.
func markerAcrossBlankLineFires() error {
	err := errors.New("x")
	// no-report: not adjacent

	if err != nil { // want `ERC001:.*err=err`
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

// bare local Error does not capture: name-trust is dead.
func bareLocalNotCapture() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		Error("auth", "bad creds", err, nil, "auth.creds")
	}
	return errors.New("noop")
}

// report.Flush is in the package but is not a capture export.
func flushNotCapture() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		report.Flush()
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

// reported death: a printing terminal carrying the checked error is a handled
// branch - log.Fatal never returns and prints the error.
func okFatalCarriesErr() error {
	err := errors.New("x")
	if err != nil {
		log.Fatal(err)
	}
	return errors.New("noop")
}

func okFatalfCarriesErr() error {
	err := errors.New("x")
	if err != nil {
		log.Fatalf("boot failed: %v", err)
	}
	return errors.New("noop")
}

// die is in the printing-terminal set; carrying the error is a reported death.
func okDieCarriesErr() error {
	err := errors.New("x")
	if err != nil {
		die(err)
	}
	return errors.New("noop")
}

// static message drops the live error - not a reported death.
func fatalStaticMsgFires() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		log.Fatal("boot failed")
	}
	return errors.New("noop")
}

// log.Printf is not terminal: the branch falls through, the error is unhandled.
func printfNotTerminalFires() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		log.Printf("failed: %v", err)
	}
	return errors.New("noop")
}

// os.Exit is excluded from printing terminals: it does not print its argument,
// so name-based ArgFlows must not accept os.Exit(len(err.Error())) as a report.
func exitCarryingErrFires() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		os.Exit(len(err.Error()))
	}
	return errors.New("noop")
}

// --- type-gate (F5): only guards of an error-assignable identifier are err-branches ---

type parseErr struct{ msg string }

func (e *parseErr) Error() string { return e.msg }

// *parseErr implements error: guarding it is an err-branch, so an unhandled
// body still fires even though the identifier is not named "err".
func typeGateConcreteErrFires(e *parseErr) error {
	if e != nil { // want `ERC001:.*err=e`
		_ = "swallowed"
	}
	return nil
}

// a non-error pointer is not an err-branch: guarding it and falling through
// must not fire (the thrift-nats false-positive class: `if conn != nil`).
func typeGateNonErrorClean(conn *int) {
	if conn != nil {
		_ = "not an error branch"
	}
}

// --- chain-preserving propagation (F5): the returned error must carry the cause ---

// %w wrap carries the caught error into the unwrap chain: propagation.
func okWrapW() error {
	err := errors.New("x")
	if err != nil {
		return fmt.Errorf("ctx: %w", err)
	}
	return errors.New("noop")
}

// errors.Join carries the caught error: propagation.
func okJoin() error {
	err := errors.New("x")
	if err != nil {
		return errors.Join(errors.New("ctx"), err)
	}
	return errors.New("noop")
}

// %v stringifies the error and breaks the unwrap chain: rethrow without cause.
func wrapVFires() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		return fmt.Errorf("ctx: %v", err)
	}
	return errors.New("noop")
}

// err.Error() flattens the error into a string: the chain is broken.
func errStringFires() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		return errors.New("ctx: " + err.Error())
	}
	return errors.New("noop")
}

// --- object-flow propagation (F5b): the err OBJECT reaching an error carrier ---

type wrapErr struct{ Cause error }

func (w *wrapErr) Error() string { return "wrap" }
func (w *wrapErr) Unwrap() error { return w.Cause }

func newWrap(cause any) error {
	if e, ok := cause.(error); ok {
		return &wrapErr{Cause: e}
	}
	return &wrapErr{}
}

// composite literal carrying the err object: propagation (Unwrap contract of
// the wrapper is trusted, not verified).
func okCompositeWrap() error {
	err := errors.New("x")
	if err != nil {
		return &wrapErr{Cause: err}
	}
	return errors.New("noop")
}

// constructor call carrying the err object: propagation.
func okConstructorWrap() error {
	err := errors.New("x")
	if err != nil {
		return newWrap(err)
	}
	return errors.New("noop")
}

// constructor fed the stringified error: every err occurrence is a string, so
// the chain is broken.
func constructorStringFires() error {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		return newWrap(err.Error())
	}
	return errors.New("noop")
}

// two-step wrap resolved against the branch: propagation.
func okTwoStepWrap() error {
	err := errors.New("x")
	if err != nil {
		wrapped := fmt.Errorf("ctx: %w", err)
		return wrapped
	}
	return errors.New("noop")
}

// --- tuple-returning call propagation (F5c): arity must not matter ---

func failNamed(cause error) (int, error) {
	return 0, &wrapErr{Cause: cause}
}

func codeMsg(err error) (int, string) {
	return 1, err.Error()
}

// a tuple-returning call with an error component carrying the err object:
// propagation, same trust as a single-result constructor.
func okTupleNamed() (int, error) {
	err := errors.New("x")
	if err != nil {
		return failNamed(err)
	}
	return 1, nil
}

// a closure wrapper is the same case: the call's tuple type decides, the
// callee is never resolved.
func okTupleClosure() (int, error) {
	fail := func(cause error) (int, error) {
		return 0, &wrapErr{Cause: cause}
	}
	err := errors.New("x")
	if err != nil {
		return fail(err)
	}
	return 1, nil
}

// a tuple without an error component cannot carry the err object out.
func tupleNoErrorFires() (int, string) {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		return codeMsg(err)
	}
	return 1, "ok"
}

// only the stringified err reaches the tuple call: the chain is broken.
func tupleStringifiedFires() (int, error) {
	err := errors.New("x")
	if err != nil { // want `ERC001:.*err=err`
		return failNamed(errors.New(err.Error()))
	}
	return 1, nil
}

// --- error-typed single result: the ERC004 exemption must not open a swallow ---

// returning the no-error value while the caught error is live drops it: nil
// carries no err object, ERC001 owns this regardless of ERC004.
func concreteErrReturnNilFires(e *parseErr) *wrapErr {
	if e != nil { // want `ERC001:.*err=e`
		return nil
	}
	return &wrapErr{}
}

// --- errors.As aliases (F5d): the derived binding is the same error object ---

// the terminal prints the errors.As-derived alias: a reported death of the
// guarded error.
func okFatalViaAsAlias() error {
	err := errors.New("x")
	if err != nil {
		var pe *parseErr
		errors.As(err, &pe)
		log.Fatalf("parse failure: %v", pe)
	}
	return errors.New("noop")
}

// an alias derived from a DIFFERENT error carries nothing of the guarded one.
func aliasOfOtherErrFires() error {
	err := errors.New("x")
	other := errors.New("y")
	if err != nil { // want `ERC001:.*err=err`
		var pe *parseErr
		errors.As(other, &pe)
		log.Fatalf("other failure: %v", pe)
	}
	return errors.New("noop")
}

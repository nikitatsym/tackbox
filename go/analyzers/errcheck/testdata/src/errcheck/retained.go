package errcheck

import "errors"

type diagnostic struct{ Detail error }
type result struct{ Diagnostics []diagnostic }
type decoy struct {
	Cause any
	Real  error
}

func retainedConstructor(cause error) *diagnostic  { return &diagnostic{Detail: cause} }
func discardedConstructor(cause error) *diagnostic { _ = cause; return &diagnostic{} }
func counterfeitCapture(any)                       {}

func retainedLiteral() []diagnostic {
	err := errors.New("original")
	if err != nil {
		return []diagnostic{{Detail: err}}
	}
	return nil
}
func retainedHelper() *diagnostic {
	err := errors.New("original")
	if err != nil {
		return retainedConstructor(err)
	}
	return nil
}
func retainedAppend(flag bool) result {
	item := result{}
	err := errors.New("original")
	if err != nil {
		item.Diagnostics = append(item.Diagnostics, diagnostic{Detail: err})
	}
	if flag {
		item.Diagnostics = append(item.Diagnostics, diagnostic{})
	}
	return item
}
func retainedField() diagnostic {
	item := diagnostic{}
	err := errors.New("original")
	if err != nil {
		item.Detail = err
	}
	return item
}
func discardedLiteral() {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		_ = diagnostic{Detail: err}
	}
}
func unusedLocal() {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		item := diagnostic{Detail: err}
		_ = item
	}
}
func overwritten() diagnostic {
	item := diagnostic{}
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		item.Detail = err
	}
	item.Detail = nil
	return item
}
func conditionalLoss(flag bool) diagnostic {
	item := diagnostic{}
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		item.Detail = err
	}
	if flag {
		item = diagnostic{}
	}
	return item
}
func wrongError() diagnostic {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		return diagnostic{Detail: errors.New("other")}
	}
	return diagnostic{}
}
func anyFieldDecoy() decoy {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		return decoy{Cause: err, Real: errors.New("other")}
	}
	return decoy{}
}
func stringOnly() string {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		return err.Error()
	}
	return ""
}
func ignoredHelper() *diagnostic {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		return discardedConstructor(err)
	}
	return nil
}
func counterfeit() {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		counterfeitCapture(diagnostic{Detail: err})
	}
}
func overwrittenIdentity() diagnostic {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		err = errors.New("replacement")
		return diagnostic{Detail: err}
	}
	return diagnostic{}
}
func shadowedIdentity() diagnostic {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		err := errors.New("shadow")
		return diagnostic{Detail: err}
	}
	return diagnostic{}
}

func mutateDiagnostic(value *diagnostic) { value.Detail = nil }
func mutationDropsRetention() *diagnostic {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		item := &diagnostic{Detail: err}
		mutateDiagnostic(item)
		return item
	}
	return nil
}

type namedDecoy struct{ Cause string }

func fieldNameDoesNotCapture() namedDecoy {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		return namedDecoy{Cause: err.Error()}
	}
	return namedDecoy{}
}

func inconsistentConstructor(cause error, discard bool) *diagnostic {
	if discard {
		return &diagnostic{}
	}
	return &diagnostic{Detail: cause}
}
func conditionalConstructorDropsError(discard bool) *diagnostic {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		return inconsistentConstructor(err, discard)
	}
	return nil
}

func truncatedDiagnosticSlice() []diagnostic {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		values := []diagnostic{{Detail: err}}
		return values[:0]
	}
	return nil
}

func lostBeforeReturn(flag bool) diagnostic {
	err := errors.New("original")
	if err != nil { // want `ERC001:.*err=err`
		if flag {
			panic("unrelated")
		}
		return diagnostic{Detail: err}
	}
	return diagnostic{}
}

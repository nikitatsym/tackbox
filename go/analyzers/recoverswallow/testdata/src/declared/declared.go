package declared

// myPanic is declared in .tackbox-reporters (installed by the test). It counts
// only when the recovered value flows into its arguments (argument-flow).
func myPanic(name string, recovered any) {}

// clean: recovered value flows into the declared sink.
func okDeclaredReport() {
	defer func() {
		if r := recover(); r != nil {
			myPanic("task", r)
		}
	}()
}

// finding: declared sink called without the recovered value.
func declaredNoArgFlowFires() {
	defer func() {
		if r := recover(); r != nil { // want `ERC007:.*recovered`
			myPanic("task", nil)
		}
	}()
}

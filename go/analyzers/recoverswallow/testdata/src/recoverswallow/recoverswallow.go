package recoverswallow

import (
	"log"

	"github.com/nikitatsym/tackbox/go/report"
)

// clean: recovered value reported via a go/report panic-capture (tier-1).
func okReport() {
	defer func() {
		if r := recover(); r != nil {
			report.Panic("task", r)
		}
	}()
}

// clean: recovered value re-panicked so the original stack survives.
func okRepanic() {
	defer func() {
		if r := recover(); r != nil {
			panic(r)
		}
	}()
}

// clean: marker with a reason suppresses.
func okMarker() {
	defer func() {
		// no-report: shutdown path, nothing left to report
		if r := recover(); r != nil {
			log.Println("recovered", r)
		}
	}()
}

// clean: assignment form, reported after an early-return guard.
func okAssignForm() {
	defer func() {
		r := recover()
		if r == nil {
			return
		}
		report.Panic("task", r)
	}()
}

// finding: recovered value swallowed (logged, not reported or re-panicked).
func swallow() {
	defer func() {
		if r := recover(); r != nil { // want `ERC007:.*recovered`
			log.Println("recovered")
		}
	}()
}

// finding: bare recover() discards the panic value.
func discard() {
	defer func() {
		recover() // want `ERC007:.*recovered`
	}()
}

// finding: marker without a reason does not suppress.
func markerNoReason() {
	defer func() {
		// no-report:
		if r := recover(); r != nil { // want `ERC007:.*recovered`
			log.Println("recovered")
		}
	}()
}

// finding: assignment form, recovered value swallowed.
func swallowAssignForm() {
	defer func() {
		r := recover() // want `ERC007:.*recovered`
		_ = r
	}()
}

package params

import _ "unsafe"

func unused(x int) int { return 1 } // want "ERC011: unused parameter x:"
func dead(x int) int { return 1 }
func recursive(x int) int { return recursive(1) } // want "ERC011: unused parameter x:"
func used(x int) int { return x }
func exported(x int) int { return 1 }
var forced func(int) int = exported
func Public(x int) int { return 1 }
func empty(x int) {}
func panicOnly(x int) { panic("unimplemented") }
func blank(_ int) int { return 1 } // want "ERC011: unused parameter _:"
func unnamed(int) int { return 1 } // want "ERC011: unused parameter \\(unnamed\\):"
func forcedBlank(_ int) int { return 1 }
func forcedUnnamed(int) int { return 1 }
var blankValue func(int) int = forcedBlank
var unnamedValue func(int) int = forcedUnnamed
func assembly(x int)

//go:linkname linked params.linked
func linked(x int) int { return 1 }

//export cgoRoot
func cgoRoot(x int) int { return 1 }

//go:wasmexport wasmRoot
func wasmRoot(x int) int { return 1 }

type I interface { constrained(int) }
type T struct{}
func (T) constrained(x string) int { return 1 }

type BlankInterface interface { constrainedBlank(int) int }
type UnnamedInterface interface { constrainedUnnamed(int) int }
func (T) constrainedBlank(_ int) int { return 1 }
func (T) constrainedUnnamed(int) int { return 1 }
var blankInterface BlankInterface = T{}
var unnamedInterface UnnamedInterface = T{}

type G[P any] struct{}
func (G[P]) value(x P) int { return 1 }
func (G[P]) direct(x P) int { return 1 } // want "ERC011: unused parameter x:"
var genericForced func(int) int = G[int]{}.value

func generic[P any](x P) int { return 1 } // want "ERC011: unused parameter x:"

func Use() {
	unused(1)
	recursive(1)
	used(1)
	Public(1)
	empty(1)
	panicOnly(1)
	blank(1)
	unnamed(1)
	assembly(1)
	linked(1)
	cgoRoot(1)
	wasmRoot(1)
	T{}.constrained("a")
	blankValue(1)
	unnamedValue(1)
	blankInterface.constrainedBlank(1)
	unnamedInterface.constrainedUnnamed(1)
	G[int]{}.direct(1)
	generic[int](1)
	var literal func(int) int = func(x int) int { return 1 }
	literal(1)
}

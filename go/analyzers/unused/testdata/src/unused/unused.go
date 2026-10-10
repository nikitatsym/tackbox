package unused

import _ "net/http/pprof"

func abandoned() {} // want `ERC010: func abandoned is unused \(U1000\)`

type abandonedType int // want `ERC010: type abandonedType is unused \(U1000\)`

const abandonedConst = 1 // want `ERC010: const abandonedConst is unused \(U1000\)`

var abandonedVar = 1 // want `ERC010: var abandonedVar is unused \(U1000\)`

type Public struct {
	abandonedField int // want `ERC010: field abandonedField is unused \(U1000\)`
	ExportedField  int
	_              int
}

func (Public) abandonedMethod() {} // want `ERC010: func Public.abandonedMethod is unused \(U1000\)`

func _abandoned() {} // want `ERC010: func _abandoned is unused \(U1000\)`

type _abandonedType int // want `ERC010: type _abandonedType is unused \(U1000\)`

const _abandonedConst = 1 // want `ERC010: const _abandonedConst is unused \(U1000\)`

type UnderscoreFields struct {
	_abandonedField int // want `ERC010: field _abandonedField is unused \(U1000\)`
}

func Exported(_ int, ignored int) {}

type ExportedType int

const ExportedConst = 1

var ExportedVar = 1

const _ = 1

var _ = usedByBlank

func usedByBlank() {}

func _() {}

type _ struct{}

func CalledFromSibling() {
	usedFromSibling()
}

package buildvariants

func withTaggedTwin(_ int) int { return 1 }
func withSuffixTwin(int) int { return 1 }
func withoutTwin(_ int) int { return 1 } // want "ERC011: unused parameter _:"
func unnamedWithoutTwin(int) int { return 1 } // want "ERC011: unused parameter \\(unnamed\\):"
func namedWithTwin(value int) int { return 1 } // want "ERC011: unused parameter value:"

type receiver struct{}
func (receiver) withMethodTwin(_ int) int { return 1 }
func (receiver) withoutMethodTwin(_ int) int { return 1 } // want "ERC011: unused parameter _:"

func Use() {
	withTaggedTwin(1)
	withSuffixTwin(1)
	withoutTwin(1)
	unnamedWithoutTwin(1)
	namedWithTwin(1)
	receiver{}.withMethodTwin(1)
	receiver{}.withoutMethodTwin(1)
}

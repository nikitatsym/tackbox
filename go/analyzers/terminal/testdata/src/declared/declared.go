package declared

import (
	"fmt"
	"os"
)

// myDie is declared (installed by the test): the body is the trust boundary,
// reviewed at declaration time - analyzers do not look inside (B3c). The
// os.Exit below would otherwise demand a capture above it.
func myDie(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(3)
}

// Package markers parses suppression comments that consumers attach in the
// comment block directly above a branch or return to opt out of an
// err-coverage rule. A marker is recognized on any line of that adjacent
// block and must carry a reason of at least MinReason characters after the
// colon.
package markers

import (
	"go/ast"
	"go/token"
	"strings"
)

// MinReason is the floor on a suppression marker's reason length after
// trimming (D009): non-empty was too cheap (`ok` / `todo` passed). No
// keyword bans - length is a structural nudge; substance is judged at review.
const MinReason = 10

type Kind int

const (
	NoReport Kind = iota
	ParseSkip
	NilReturn
	TestSkip
)

func (k Kind) String() string {
	switch k {
	case NoReport:
		return "no-report"
	case ParseSkip:
		return "parse-skip"
	case NilReturn:
		return "nil-return"
	case TestSkip:
		return "test-skip"
	}
	return ""
}

type Marker struct {
	Kind   Kind
	Reason string
	Pos    token.Pos
}

type Index struct {
	file   *token.File
	groups []group
}

// group records a comment block's last line and the marker nearest that end
// (closest to a node placed directly below), if the block carries one.
type group struct {
	lastLine  int
	marker    Marker
	hasMarker bool
}

func Build(fset *token.FileSet, f *ast.File) *Index {
	tf := fset.File(f.Pos())
	idx := &Index{file: tf}
	for _, cg := range f.Comments {
		g := group{lastLine: tf.Line(cg.End())}
		for _, c := range cg.List {
			if m, ok := parse(c); ok {
				g.marker, g.hasMarker = m, true // later comment wins: nearest the node
			}
		}
		idx.groups = append(idx.groups, g)
	}
	return idx
}

var prefixes = []struct {
	kind   Kind
	prefix string
}{
	{NoReport, "no-report:"},
	{ParseSkip, "parse-skip:"},
	{NilReturn, "nil-return:"},
	{TestSkip, "test-skip:"},
}

func parse(c *ast.Comment) (Marker, bool) {
	if !strings.HasPrefix(c.Text, "//") {
		return Marker{}, false
	}
	text := strings.TrimSpace(strings.TrimPrefix(c.Text, "//"))
	for _, p := range prefixes {
		if strings.HasPrefix(text, p.prefix) {
			reason := strings.TrimSpace(strings.TrimPrefix(text, p.prefix))
			if len(reason) < MinReason {
				return Marker{}, false
			}
			return Marker{Kind: p.kind, Reason: reason, Pos: c.Slash}, true
		}
	}
	return Marker{}, false
}

// Above returns the marker carried by the comment block directly above node.
// The marker may sit on any line of that block, not only the line immediately
// above, so a reason too long for one line can be followed by human context.
func (idx *Index) Above(node ast.Node) (Marker, bool) {
	if node == nil || idx.file == nil {
		return Marker{}, false
	}
	line := idx.file.Line(node.Pos())
	for _, g := range idx.groups {
		if g.lastLine == line-1 && g.hasMarker {
			return g.marker, true
		}
	}
	return Marker{}, false
}

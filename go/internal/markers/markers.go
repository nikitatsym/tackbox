// Package markers parses suppression comments that consumers attach
// directly above a branch or return to opt out of an err-coverage
// rule. A marker is only recognized on the line immediately above
// the target node and must carry a non-empty reason after the colon.
package markers

import (
	"go/ast"
	"go/token"
	"strings"
)

type Kind int

const (
	NoSentry Kind = iota
	ParseSkip
	NilReturn
)

func (k Kind) String() string {
	switch k {
	case NoSentry:
		return "no-sentry"
	case ParseSkip:
		return "parse-skip"
	case NilReturn:
		return "nil-return"
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
	byLine map[int]Marker
}

func Build(fset *token.FileSet, f *ast.File) *Index {
	tf := fset.File(f.Pos())
	idx := &Index{file: tf, byLine: make(map[int]Marker)}
	for _, cg := range f.Comments {
		for _, c := range cg.List {
			m, ok := parse(c)
			if !ok {
				continue
			}
			idx.byLine[tf.Line(c.Slash)] = m
		}
	}
	return idx
}

var prefixes = []struct {
	kind   Kind
	prefix string
}{
	{NoSentry, "no-sentry:"},
	{ParseSkip, "parse-skip:"},
	{NilReturn, "nil-return:"},
}

func parse(c *ast.Comment) (Marker, bool) {
	if !strings.HasPrefix(c.Text, "//") {
		return Marker{}, false
	}
	text := strings.TrimSpace(strings.TrimPrefix(c.Text, "//"))
	for _, p := range prefixes {
		if strings.HasPrefix(text, p.prefix) {
			reason := strings.TrimSpace(strings.TrimPrefix(text, p.prefix))
			if reason == "" {
				return Marker{}, false
			}
			return Marker{Kind: p.kind, Reason: reason, Pos: c.Slash}, true
		}
	}
	return Marker{}, false
}

// Above returns the marker placed on the line directly above node.
func (idx *Index) Above(node ast.Node) (Marker, bool) {
	if node == nil || idx.file == nil {
		return Marker{}, false
	}
	line := idx.file.Line(node.Pos())
	m, ok := idx.byLine[line-1]
	return m, ok
}

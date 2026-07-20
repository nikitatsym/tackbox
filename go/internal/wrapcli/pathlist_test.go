package wrapcli

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// A list-file written on Windows carries CRLF line endings; a trailing `\r`
// left on a path breaks every downstream consumer ("File not found: a.go\r").
func TestReadPathListStripsCRLF(t *testing.T) {
	list := filepath.Join(t.TempDir(), "paths.txt")
	if err := os.WriteFile(list, []byte("a/b.go\r\npkg/c.go\r\n\r\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	got, err := ReadPathList(list)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"a/b.go", "pkg/c.go"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

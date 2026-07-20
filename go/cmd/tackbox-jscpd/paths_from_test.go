package main

import (
	"os"
	"path/filepath"
	"testing"
)

// --paths-from feeds the file set through a list-file instead of positional
// argv, so parseArgs must read it into files (order preserved) and never leave
// the flag or its path among the files.
func TestParseArgsReadsPathsFrom(t *testing.T) {
	dir := t.TempDir()
	list := filepath.Join(dir, "list.txt")
	if err := os.WriteFile(list, []byte("x.go\nz.java\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	_, jscpd, files, err := parseArgs([]string{"--jscpd", "/bin/jscpd", "--paths-from", list})
	if err != nil {
		t.Fatalf("parseArgs: %v", err)
	}
	if jscpd != "/bin/jscpd" {
		t.Fatalf("jscpd = %q, want /bin/jscpd", jscpd)
	}
	want := []string{"x.go", "z.java"}
	if len(files) != len(want) || files[0] != want[0] || files[1] != want[1] {
		t.Fatalf("files = %v, want %v", files, want)
	}
}

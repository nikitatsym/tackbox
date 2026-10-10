package main_test

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestUnusedPackageAndTestVariants(t *testing.T) {
	bin := buildErclint(t, "test")
	repo := t.TempDir()
	files := map[string]string{
		"go.mod":         "module unused-integration\n\ngo 1.27.0\n",
		"edited.go":      "package example\n\nfunc UsedElsewhere() { usedBySibling() }\nfunc dead() {}\nfunc testOnly() {}\n",
		"sibling.go":     "package example\n\nfunc usedBySibling() {}\nfunc onlyExcluded() {}\n",
		"edited_test.go": "package example\n\nimport \"testing\"\n\nfunc TestUse(t *testing.T) { testOnly() }\nfunc deadTestHelper() {}\n",
	}
	for name, content := range files {
		if err := os.WriteFile(filepath.Join(repo, name), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	list := filepath.Join(t.TempDir(), "packages.txt")
	if err := os.WriteFile(list, []byte(".\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(bin, "-json", "-unused", "--paths-from", list)
	cmd.Dir = repo
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("run erclint: %v\nstderr: %s", err, stderr.String())
	}
	var result map[string]map[string][]struct {
		Posn    string `json:"posn"`
		Message string `json:"message"`
	}
	if err := json.Unmarshal(out, &result); err != nil {
		t.Fatalf("decode erclint output: %v\n%s", err, out)
	}
	counts := map[string]int{}
	for _, analyzers := range result {
		for _, finding := range analyzers["unused"] {
			counts[strings.SplitN(finding.Message, ":", 2)[0]+":"+strings.TrimPrefix(finding.Posn, repo+string(os.PathSeparator))]++
			if !strings.HasPrefix(finding.Posn, repo+string(os.PathSeparator)) {
				t.Errorf("finding lost file attribution: %s", finding.Posn)
			}
		}
	}
	want := map[string]int{
		"ERC010:edited.go:4:6":      1,
		"ERC010:edited.go:5:6":      1,
		"ERC010:sibling.go:4:6":     1,
		"ERC010:edited_test.go:6:6": 1,
	}
	if len(counts) != len(want) {
		t.Fatalf("unexpected findings: %v\n%s", counts, out)
	}
	for message, count := range want {
		if counts[message] != count {
			t.Errorf("%s: got %d findings, want %d\n%s", message, counts[message], count, out)
		}
	}
}

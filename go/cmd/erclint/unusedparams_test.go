package main_test

import (
	"bytes"
	"encoding/json"
	"io"
	"maps"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestUnusedParamsLoadedVariants(t *testing.T) {
	bin := buildErclint(t, "test")
	plain := "variant-fixture|plain|p.go:2:13|ERC011: unused parameter x"
	for _, tc := range []struct {
		name         string
		testSource   string
		args         []string
		wantPresence bool
		want         []string
	}{
		{"internal-zero", "package p\nimport \"testing\"\nvar forced func(int) int = helper\nfunc TestUse(t *testing.T) { forced(1) }\n", nil, true, []string{plain}},
		{"test-disabled", "package p\nimport \"testing\"\nvar forced func(int) int = helper\nfunc TestUse(t *testing.T) { forced(1) }\n", []string{"-test=false"}, false, []string{plain}},
		{"tag-excluded", "//go:build special\n\npackage p\nimport \"testing\"\nvar forced func(int) int = helper\nfunc TestUse(t *testing.T) { forced(1) }\n", nil, false, []string{plain}},
		{"tag-selected", "//go:build special\n\npackage p\nimport \"testing\"\nvar forced func(int) int = helper\nfunc TestUse(t *testing.T) { forced(1) }\n", nil, true, []string{plain}},
		{"external-only", "package p_test\nimport \"testing\"\nfunc external(x int) int { return 1 }\nfunc TestUse(t *testing.T) { external(1) }\n", nil, false, []string{plain, "variant-fixture_test|external-test|p_test.go:3:15|ERC011: unused parameter x"}},
		{"internal-findings", "package p\nimport \"testing\"\nfunc testHelper(y int) int { return 1 }\nfunc TestUse(t *testing.T) { helper(1); testHelper(1) }\n", nil, true, []string{plain, "variant-fixture|internal-test|p.go:2:13|ERC011: unused parameter x", "variant-fixture|internal-test|p_test.go:3:17|ERC011: unused parameter y"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			repo := t.TempDir()
			for name, content := range map[string]string{
				"go.mod":    "module variant-fixture\n\ngo 1.27.0\n",
				"p.go":      "package p\nfunc helper(x int) int { return 1 }\nfunc Public() { helper(1) }\n",
				"p_test.go": tc.testSource,
			} {
				if err := os.WriteFile(filepath.Join(repo, name), []byte(content), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			args := append([]string{"-json", "-unusedparams"}, tc.args...)
			cmd := exec.Command(bin, append(args, ".")...)
			cmd.Dir = repo
			if tc.name == "tag-selected" {
				cmd.Env = append(os.Environ(), "GOFLAGS=-tags=special")
			}
			out, err := cmd.CombinedOutput()
			if err != nil {
				t.Fatalf("erclint: %v\n%s", err, out)
			}
			decoder := json.NewDecoder(bytes.NewReader(out))
			presence := false
			got := map[string]int{}
			for {
				var doc map[string]map[string][]struct {
					Record  string `json:"record"`
					Variant string `json:"variant"`
					Posn    string `json:"posn"`
					Message string `json:"message"`
				}
				if err := decoder.Decode(&doc); err == io.EOF {
					break
				} else if err != nil {
					t.Fatalf("decode: %v\n%s", err, out)
				}
				for pkg, analyzers := range doc {
					for _, item := range analyzers["unusedparams"] {
						if item.Record != "" {
							if item.Record != "erc011-package-variant-v1" || pkg != "variant-fixture" || item.Variant != "internal-test" {
								t.Fatalf("invalid control: %s", out)
							}
							presence = true
							continue
						}
						location := strings.TrimPrefix(item.Posn, repo+string(os.PathSeparator))
						parts := strings.SplitN(item.Message, ":", 3)
						if len(parts) < 3 {
							t.Fatalf("invalid rule/parameter identity: %s", out)
						}
						got[pkg+"|"+item.Variant+"|"+location+"|"+parts[0]+":"+parts[1]]++
					}
				}
			}
			want := map[string]int{}
			for _, identity := range tc.want {
				want[identity]++
			}
			if presence != tc.wantPresence || !maps.Equal(got, want) {
				t.Fatalf("presence/findings = %v/%v, want %v/%v\n%s", presence, got, tc.wantPresence, want, out)
			}
		})
	}
}

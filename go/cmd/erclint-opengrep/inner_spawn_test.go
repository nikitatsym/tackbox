package main_test

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// writeOpengrepStub writes a stub named "opengrep" (found via PATH) that records
// each scan target it is handed to OPENGREP_STUB_RECORD and exits 0; it never
// runs a real scan. Returns the dir to prepend to PATH.
func writeOpengrepStub(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	script := "#!/bin/sh\n" +
		"rec=\"$OPENGREP_STUB_RECORD\"\n" +
		"skip=0\n" +
		"for a in \"$@\"; do\n" +
		"  if [ \"$skip\" = \"1\" ]; then skip=0; continue; fi\n" +
		"  case \"$a\" in\n" +
		"    scan|--error|--json) ;;\n" +
		"    --config) skip=1 ;;\n" +
		"    *) printf '%s\\n' \"$a\" >> \"$rec\" ;;\n" +
		"  esac\n" +
		"done\n" +
		"exit 0\n"
	if err := os.WriteFile(filepath.Join(dir, "opengrep"), []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return dir
}

// manyFakePaths returns absolute path strings totaling more than minBytes. The
// opengrep wrapper never reads target files (opengrep would), so fakes suffice.
func manyFakePaths(minBytes int) []string {
	seg := "/nonexistent" + strings.Repeat("/"+strings.Repeat("d", 200), 3)
	var paths []string
	total := 0
	for i := 0; total <= minBytes; i++ {
		p := fmt.Sprintf("%s/file_%08d.go", seg, i)
		paths = append(paths, p)
		total += len(p) + 1
	}
	return paths
}

func TestInnerSpawnChunksHugeTargetSet(t *testing.T) {
	bin := buildOpengrepWrapper(t)
	stubDir := writeOpengrepStub(t)
	paths := manyFakePaths(1 << 20)

	total := 0
	for _, p := range paths {
		total += len(p) + 1
	}
	if total <= (1 << 20) {
		t.Fatalf("path bytes %d did not exceed 1 MiB", total)
	}

	repo := makeRepo(t) // clean cwd (no .semgrepignore)
	listFile := filepath.Join(repo, "list.txt")
	if err := os.WriteFile(listFile, []byte(strings.Join(paths, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	record := filepath.Join(t.TempDir(), "targets.txt")

	cmd := exec.Command(bin, "--paths-from", listFile)
	cmd.Dir = repo
	cmd.Env = stubPathEnv(stubDir, record)
	var stderr strings.Builder
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("wrapper inner spawn failed (E2BIG?): %v\nstderr: %s", err, stderr.String())
	}

	got := map[string]int{}
	f, err := os.Open(record)
	if err != nil {
		t.Fatalf("stub recorded nothing: %v", err)
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		got[strings.TrimSpace(sc.Text())]++
	}
	if err := sc.Err(); err != nil {
		t.Fatalf("scan record: %v", err)
	}
	if len(got) != len(paths) {
		t.Fatalf("opengrep saw %d distinct targets across batches, want %d", len(got), len(paths))
	}
	for _, p := range paths {
		if got[p] != 1 {
			t.Fatalf("target %s reached opengrep %d times, want 1", p, got[p])
		}
	}
}

// writeOpengrepFindingStub writes an "opengrep" that, in --json mode, emits one
// result per scan target (exit 1, findings) so the test can verify the wrapper
// merges machine findings across batches exactly as a single scan would.
func writeOpengrepFindingStub(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	script := "#!/bin/sh\n" +
		"json=0; skip=0; targets=\"\"\n" +
		"for a in \"$@\"; do\n" +
		"  if [ \"$skip\" = \"1\" ]; then skip=0; continue; fi\n" +
		"  case \"$a\" in\n" +
		"    --json) json=1 ;;\n" +
		"    --config) skip=1 ;;\n" +
		"    scan|--error) ;;\n" +
		"    *) targets=\"$targets $a\" ;;\n" +
		"  esac\n" +
		"done\n" +
		"if [ \"$json\" = \"1\" ]; then\n" +
		"  printf '{\"results\":['\n" +
		"  sep=\"\"\n" +
		"  for t in $targets; do\n" +
		"    printf '%s{\"check_id\":\"r.go-exit-in-recover\",\"path\":\"%s\",\"start\":{\"line\":1},\"extra\":{\"message\":\"m\"}}' \"$sep\" \"$t\"\n" +
		"    sep=\",\"\n" +
		"  done\n" +
		"  printf ']}'\n" +
		"fi\n" +
		"exit 1\n"
	if err := os.WriteFile(filepath.Join(dir, "opengrep"), []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return dir
}

func TestMachineFindingsMergeAcrossBatches(t *testing.T) {
	bin := buildOpengrepWrapper(t)
	stubDir := writeOpengrepFindingStub(t)
	// Enough targets to span more than one batch (> maxScanArgvBytes), but small
	// enough to stay fast; the merge, not ARG_MAX, is under test here.
	paths := manyFakePaths(300 * 1024)

	repo := makeRepo(t)
	listFile := filepath.Join(repo, "list.txt")
	if err := os.WriteFile(listFile, []byte(strings.Join(paths, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(bin, "--machine", "--paths-from", listFile)
	cmd.Dir = repo
	cmd.Env = stubPathEnv(stubDir, filepath.Join(t.TempDir(), "unused"))
	var stdout, stderr strings.Builder
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()

	// Findings mean exit 1; a non-ExitError would be an invocation failure.
	if ee, ok := err.(*exec.ExitError); !ok || ee.ExitCode() != 1 {
		t.Fatalf("want exit 1 (merged findings), got %v\nstderr: %s", err, stderr.String())
	}

	// Exactly one machine finding per target, deduped, across all batches - the
	// same set a single scan would emit.
	seen := map[string]int{}
	for _, line := range strings.Split(strings.TrimSpace(stdout.String()), "\n") {
		if line == "" {
			continue
		}
		var f struct {
			File string `json:"file"`
			Rule string `json:"rule"`
		}
		if err := json.Unmarshal([]byte(line), &f); err != nil {
			t.Fatalf("machine line not json: %v\n%q", err, line)
		}
		if f.Rule != "go-exit-in-recover" {
			t.Fatalf("unexpected rule %q", f.Rule)
		}
		seen[f.File]++
	}
	if len(seen) != len(paths) {
		t.Fatalf("merged %d distinct findings, want %d (batch merge lost/dup findings)", len(seen), len(paths))
	}
	for _, p := range paths {
		if seen[p] != 1 {
			t.Fatalf("finding for %s appeared %d times, want 1", p, seen[p])
		}
	}
}

// stubPathEnv is the parent env with PATH prepended by stubDir and the record
// file exported, so the wrapper resolves "opengrep" to the stub.
func stubPathEnv(stubDir, record string) []string {
	env := append([]string(nil), os.Environ()...)
	newPath := "PATH=" + stubDir + string(os.PathListSeparator) + os.Getenv("PATH")
	replaced := false
	for i, e := range env {
		if strings.HasPrefix(e, "PATH=") {
			env[i] = newPath
			replaced = true
		}
	}
	if !replaced {
		env = append(env, newPath)
	}
	return append(env, "OPENGREP_STUB_RECORD="+record)
}

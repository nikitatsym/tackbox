package main

import (
	"bufio"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/nikitatsym/tackbox/go/internal/wrapcli"
)

// TestMain doubles as a jscpd stub: when TACKBOX_JSCPD_STUB is set the test
// binary copies STUB_REPORT into <--output>/jscpd-report.json and exits
// STUB_EXIT, so run() can be exercised end-to-end without the real binary.
func TestMain(m *testing.M) {
	if os.Getenv("TACKBOX_JSCPD_STUB") != "" {
		os.Exit(stubMain())
	}
	os.Exit(m.Run())
}

func stubMain() int {
	out := ""
	args := os.Args[1:]
	for i, a := range args {
		if a == "--output" && i+1 < len(args) {
			out = args[i+1]
		}
	}
	if out != "" {
		data, _ := os.ReadFile(os.Getenv("STUB_REPORT"))
		if err := os.WriteFile(filepath.Join(out, "jscpd-report.json"), data, 0o644); err != nil {
			return 3
		}
	}
	if os.Getenv("STUB_EXIT") == "1" {
		return 1
	}
	return 0
}

// mkSpanReport builds a jscpd-style report JSON for one clone of the given
// format between two absolute files with explicit line spans.
func mkSpanReport(format, fileA string, aStart, aEnd int, fileB string, bStart, bEnd int) []byte {
	ep := func(name string, start, end int) map[string]any {
		return map[string]any{
			"name": name, "start": start, "end": end,
			"startLoc": map[string]any{"line": start},
			"endLoc":   map[string]any{"line": end},
		}
	}
	doc := map[string]any{
		"duplicates": []any{map[string]any{
			"firstFile": ep(fileA, aStart, aEnd), "secondFile": ep(fileB, bStart, bEnd),
			"format": format, "tokens": 146,
		}},
	}
	b, _ := json.MarshalIndent(doc, "", "  ")
	return b
}

// mkReport is the go-format shorthand (end = start+3, arbitrary but stable).
func mkReport(fileA string, lineA int, fileB string, lineB int) []byte {
	return mkSpanReport("go", fileA, lineA, lineA+3, fileB, lineB, lineB+3)
}

// writeSrc writes a source file whose clone body starts at line `start`; the
// preceding lines are `above` (each its own source line, directly abutting the
// body), padded so the body lands exactly on `start`.
func writeSrc(t *testing.T, dir, name string, start int, above []string) string {
	t.Helper()
	var lines []string
	for len(lines) < start-1-len(above) {
		lines = append(lines, "package x")
	}
	lines = append(lines, above...)
	lines = append(lines, "func Body() { return }")
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func parseNDJSON(t *testing.T, s string) []wrapcli.Finding {
	t.Helper()
	var out []wrapcli.Finding
	sc := bufio.NewScanner(strings.NewReader(s))
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var f wrapcli.Finding
		if err := json.Unmarshal([]byte(line), &f); err != nil {
			t.Fatalf("bad NDJSON line %q: %v", line, err)
		}
		out = append(out, f)
	}
	return out
}

func emitTo(t *testing.T, cwd string, rep []byte, machine bool) (string, int) {
	t.Helper()
	var parsed jscpdReport
	if err := json.Unmarshal(rep, &parsed); err != nil {
		t.Fatalf("unmarshal report: %v", err)
	}
	var buf strings.Builder
	n, err := emit(&parsed, newFileLines(), cwd, machine, &buf)
	if err != nil {
		t.Fatalf("emit: %v", err)
	}
	return buf.String(), n
}

func TestMachineBothEndpointsReported(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.go", 5, nil)
	b := writeSrc(t, dir, "b.go", 8, nil)
	out, n := emitTo(t, dir, mkReport(a, 5, b, 8), true)
	if n != 2 {
		t.Fatalf("expected 2 surviving endpoints, got %d\n%s", n, out)
	}
	fs := parseNDJSON(t, out)
	if len(fs) != 2 {
		t.Fatalf("expected 2 NDJSON lines, got %d: %s", len(fs), out)
	}
	want := map[string]int{"a.go": 5, "b.go": 8}
	for _, f := range fs {
		if f.Rule != "DUP001" {
			t.Fatalf("rule = %q, want DUP001", f.Rule)
		}
		if want[f.File] != f.Line {
			t.Fatalf("finding %+v not in expected %v", f, want)
		}
	}
}

func TestDupOkOneEndpointSuppressed(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.go", 5, []string{"// dup-ok: fixture proves suppression"})
	b := writeSrc(t, dir, "b.go", 8, nil)
	out, n := emitTo(t, dir, mkReport(a, 5, b, 8), true)
	if n != 1 {
		t.Fatalf("expected 1 surviving endpoint, got %d\n%s", n, out)
	}
	fs := parseNDJSON(t, out)
	if len(fs) != 1 || fs[0].File != "b.go" || fs[0].Line != 8 {
		t.Fatalf("expected only b.go:8, got %v", fs)
	}
}

func TestDupOkBothEndpointsSuppressedClean(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.go", 5, []string{"// dup-ok: a side"})
	b := writeSrc(t, dir, "b.go", 8, []string{"// dup-ok: b side"})
	out, n := emitTo(t, dir, mkReport(a, 5, b, 8), true)
	if n != 0 {
		t.Fatalf("expected clean (0 survivors), got %d\n%s", n, out)
	}
	if strings.TrimSpace(out) != "" {
		t.Fatalf("expected no NDJSON, got %q", out)
	}
}

func TestDupOkEmptyReasonDoesNotSuppress(t *testing.T) {
	dir := t.TempDir()
	// Empty reason after the colon is not a valid marker: the endpoint stays.
	a := writeSrc(t, dir, "a.go", 5, []string{"// dup-ok:"})
	b := writeSrc(t, dir, "b.go", 8, nil)
	_, n := emitTo(t, dir, mkReport(a, 5, b, 8), true)
	if n != 2 {
		t.Fatalf("empty-reason dup-ok must not suppress; got %d survivors, want 2", n)
	}
}

func TestDupOkPythonHashComment(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.py", 5, []string{"# dup-ok: python side"})
	b := writeSrc(t, dir, "b.py", 8, nil)
	_, n := emitTo(t, dir, mkReport(a, 5, b, 8), true)
	if n != 1 {
		t.Fatalf("expected 1 survivor after # dup-ok, got %d", n)
	}
}

func TestDupOkMultiLineBlockMarkerAnyLine(t *testing.T) {
	dir := t.TempDir()
	// Marker on the upper line of a two-line contiguous block still counts.
	a := writeSrc(t, dir, "a.go", 6, []string{"// dup-ok: reason spanning", "// human context line"})
	b := writeSrc(t, dir, "b.go", 8, nil)
	_, n := emitTo(t, dir, mkReport(a, 6, b, 8), true)
	if n != 1 {
		t.Fatalf("expected 1 survivor when marker is above a comment block, got %d", n)
	}
}

func TestDupOkTrailingCommentNotStandalone(t *testing.T) {
	dir := t.TempDir()
	// A dup-ok trailing a code line is not a standalone block -> no suppression.
	p := filepath.Join(dir, "a.go")
	src := "package x\nx := 1 // dup-ok: trailing\nfunc Body() { return }\n"
	if err := os.WriteFile(p, []byte(src), 0o644); err != nil {
		t.Fatal(err)
	}
	b := writeSrc(t, dir, "b.go", 8, nil)
	_, n := emitTo(t, dir, mkReport(p, 3, b, 8), true)
	if n != 2 {
		t.Fatalf("trailing dup-ok must not suppress; got %d survivors, want 2", n)
	}
}

func TestHumanSummaryShowsBothEndpoints(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.go", 5, nil)
	b := writeSrc(t, dir, "b.go", 8, nil)
	out, n := emitTo(t, dir, mkReport(a, 5, b, 8), false)
	if n != 2 {
		t.Fatalf("expected 2 survivors, got %d", n)
	}
	if !strings.Contains(out, "a.go:5-8") || !strings.Contains(out, "b.go:8-11") {
		t.Fatalf("human summary missing endpoints: %q", out)
	}
	if !strings.Contains(out, "DUP001") {
		t.Fatalf("human summary missing rule id: %q", out)
	}
}

func TestReadReportMissingIsError(t *testing.T) {
	if _, err := readReport(filepath.Join(t.TempDir(), "absent.json")); err == nil {
		t.Fatal("missing report must error, not parse to empty")
	}
}

func TestReadReportGarbageIsError(t *testing.T) {
	p := filepath.Join(t.TempDir(), "jscpd-report.json")
	if err := os.WriteFile(p, []byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := readReport(p); err == nil {
		t.Fatal("unparseable report must error, never a silent clean")
	}
}

func buildWrapper(t *testing.T) string {
	t.Helper()
	bin := filepath.Join(t.TempDir(), "tackbox-jscpd")
	cmd := exec.Command("go", "build", "-o", bin, ".")
	cmd.Stdout, cmd.Stderr = os.Stderr, os.Stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("build wrapper: %v", err)
	}
	return bin
}

func runWith(t *testing.T, bin, cwd string, env []string, args ...string) (string, string, error) {
	t.Helper()
	cmd := exec.Command(bin, args...)
	cmd.Dir = cwd
	cmd.Env = append(os.Environ(), env...)
	var out, errb strings.Builder
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()
	return out.String(), errb.String(), err
}

// runStub builds the wrapper and runs it in machine mode against the TestMain
// jscpd stub, which drops `report` into the wrapper's --output dir.
func runStub(t *testing.T, repo string, report []byte, files ...string) (string, string, error) {
	t.Helper()
	bin := buildWrapper(t)
	stub, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	repFile := filepath.Join(t.TempDir(), "rep.json")
	if err := os.WriteFile(repFile, report, 0o644); err != nil {
		t.Fatal(err)
	}
	env := []string{"TACKBOX_JSCPD_STUB=1", "STUB_REPORT=" + repFile}
	return runWith(t, bin, repo, env, append([]string{"--jscpd", stub, "--machine"}, files...)...)
}

func TestRunStubHappyPath(t *testing.T) {
	repo := t.TempDir()
	a := writeSrc(t, repo, "a.go", 5, nil)
	b := writeSrc(t, repo, "b.go", 8, nil)
	out, errOut, runErr := runStub(t, repo, mkReport(a, 5, b, 8), "a.go", "b.go")
	if runErr == nil {
		t.Fatalf("expected exit 1 for surviving clones; got clean\nstdout=%s\nstderr=%s", out, errOut)
	}
	fs := parseNDJSON(t, out)
	if len(fs) != 2 {
		t.Fatalf("expected 2 findings through run(), got %d: %s", len(fs), out)
	}
}

func TestRunStubUnparseableReportNeverClean(t *testing.T) {
	repo := t.TempDir()
	writeSrc(t, repo, "a.go", 5, nil)
	out, errOut, runErr := runStub(t, repo, []byte("{garbage"), "a.go")
	if runErr == nil {
		t.Fatalf("unparseable report must be nonzero, never clean\nstdout=%s", out)
	}
	if !strings.Contains(errOut, "parse jscpd report") {
		t.Fatalf("stderr should name the parse failure: %s", errOut)
	}
}

func TestRunBadBinaryPathNeverClean(t *testing.T) {
	bin := buildWrapper(t)
	repo := t.TempDir()
	writeSrc(t, repo, "a.go", 5, nil)
	out, errOut, runErr := runWith(t, bin, repo, nil,
		"--jscpd", filepath.Join(repo, "nonexistent-jscpd"), "--machine", "a.go")
	if runErr == nil {
		t.Fatalf("a bad jscpd path must exit nonzero, never clean\nstdout=%s", out)
	}
	if !strings.Contains(errOut, "run jscpd") {
		t.Fatalf("stderr should name the spawn failure: %s", errOut)
	}
}

func TestVersionFlag(t *testing.T) {
	bin := buildWrapper(t)
	out, err := exec.Command(bin, "--version").Output()
	if err != nil {
		t.Fatalf("run --version: %v", err)
	}
	if string(out) != "tackbox-jscpd dev\n" {
		t.Fatalf("--version stdout = %q", out)
	}
}

func TestJavaHeaderEnd(t *testing.T) {
	cases := []struct {
		name string
		src  string
		want int
	}{
		{"javadoc header", "/**\n * Doc.\n */\npackage a;\n\nimport b.C;\nclass A {}", 6},
		{"block comment between imports", "package a;\n/* note\n   spans */\nimport b.C;\nclass A {}", 4},
		{"default package", "import b.C;\nimport static b.D.e;\nclass A {}", 2},
		{"annotation ends header", "package a;\nimport b.C;\n@Deprecated\nclass A {}", 2},
		{"code after block close ends header", "package a;\n/* c */ class A {}", 1},
		{"empty file", "", 1},
		{"class on first line", "class A {}", 0},
	}
	for _, tc := range cases {
		if got := javaHeaderEnd(strings.Split(tc.src, "\n")); got != tc.want {
			t.Errorf("%s: javaHeaderEnd = %d, want %d", tc.name, got, tc.want)
		}
	}
}

// javaHeaderSrc is a package + 10-import header: well over the 50-token clone
// threshold, so two copies of it alone form a reportable clone.
const javaHeaderSrc = `package fixture.rules;

import a.b.Alpha;
import a.b.Beta;
import a.b.Gamma;
import a.b.Delta;
import a.b.Epsilon;
import a.b.Zeta;
import a.b.Eta;
import a.b.Theta;
import a.b.Iota;
import a.b.Kappa;
`

// writeJava writes a fixture class under javaHeaderSrc: header lines 1-12,
// blank 13 (headerEnd = 13), class body from line 14.
func writeJava(t *testing.T, dir, name, cls string) string {
	t.Helper()
	src := javaHeaderSrc + "\npublic final class " + cls + " {\n    int one() { return 1; }\n    int two() { return 2; }\n}\n"
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, []byte(src), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestJavaHeaderOnlyCloneDropped(t *testing.T) {
	dir := t.TempDir()
	a := writeJava(t, dir, "A.java", "A")
	b := writeJava(t, dir, "B.java", "B")
	// Endpoints confined to the headers (1-12 within headerEnd 13): dropped
	// without any marker, and never present in the output.
	out, n := emitTo(t, dir, mkSpanReport("java", a, 1, 12, b, 1, 12), true)
	if n != 0 || strings.TrimSpace(out) != "" {
		t.Fatalf("header-only java clone must be dropped; got %d survivors, out=%q", n, out)
	}
}

func TestJavaHeaderPlusBodyCloneStillReported(t *testing.T) {
	dir := t.TempDir()
	a := writeJava(t, dir, "A.java", "A")
	b := writeJava(t, dir, "B.java", "B")
	// Endpoints reach past the header (line 15 > headerEnd 13): the copied
	// method body is a real finding, the filter must not eat it.
	out, n := emitTo(t, dir, mkSpanReport("java", a, 3, 15, b, 3, 15), true)
	if n != 2 {
		t.Fatalf("java clone reaching code must survive the header filter; got %d, out=%q", n, out)
	}
}

func TestGoCloneNotHeaderFiltered(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.go", 2, nil)
	b := writeSrc(t, dir, "b.go", 2, nil)
	// Same line shape as a java header clone, but format=go: the java-only
	// filter must not touch it.
	_, n := emitTo(t, dir, mkSpanReport("go", a, 1, 2, b, 1, 2), true)
	if n != 2 {
		t.Fatalf("non-java clone must not be header-filtered; got %d survivors", n)
	}
}

func TestRunIgnoreMarkerBanDUP002(t *testing.T) {
	repo := t.TempDir()
	// The banned substring is assembled from the const so neither this test
	// nor the binary source ever contains it literally.
	src := "package x\n// " + ignoreMarker + "-start\nfunc A() int { return 1 }\n// " + ignoreMarker + "-end\n"
	if err := os.WriteFile(filepath.Join(repo, "a.go"), []byte(src), 0o644); err != nil {
		t.Fatal(err)
	}
	out, errOut, runErr := runStub(t, repo, []byte(`{"duplicates": []}`), "a.go")
	if runErr == nil {
		t.Fatalf("native ignore markers must fail the run\nstdout=%s\nstderr=%s", out, errOut)
	}
	fs := parseNDJSON(t, out)
	if len(fs) != 2 {
		t.Fatalf("expected DUP002 on both marker lines, got %v", fs)
	}
	want := map[int]bool{2: true, 4: true}
	for _, f := range fs {
		if f.Rule != "DUP002" || f.File != "a.go" || !want[f.Line] {
			t.Fatalf("unexpected finding %+v (want DUP002 a.go lines 2 and 4)", f)
		}
	}
}

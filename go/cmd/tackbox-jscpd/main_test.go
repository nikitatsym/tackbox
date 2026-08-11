package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/nikitatsym/tackbox/go/internal/wrapcli"
)

// mkSpanReport builds a jscpd-style report JSON for one clone of the given
// format between two absolute files with explicit line spans.
func mkSpanReport(format, fileA string, aStart, aEnd int, fileB string, bStart, bEnd int) []byte {
	ep := func(name string, start, end int) map[string]any {
		return map[string]any{
			"name": name, "start": start, "end": end,
			"startLoc": map[string]any{"line": start, "column": 0},
			"endLoc":   map[string]any{"line": end, "column": 100},
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

func mkSubdir(t *testing.T, dir, name string) string {
	t.Helper()
	sub := filepath.Join(dir, name)
	if err := os.Mkdir(sub, 0o755); err != nil {
		t.Fatal(err)
	}
	return sub
}

func writeSrcPair(t *testing.T, dir, subdir string) (string, string) {
	t.Helper()
	sourceDir := mkSubdir(t, dir, subdir)
	return writeSrc(t, sourceDir, "a.go", 5, nil),
		writeSrc(t, sourceDir, "b.go", 8, nil)
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
	return emitZonesTo(t, cwd, rep, nil, machine)
}

func emitZonesTo(t *testing.T, cwd string, rep []byte, zones callableZones, machine bool) (string, int) {
	t.Helper()
	var parsed jscpdReport
	if err := json.Unmarshal(rep, &parsed); err != nil {
		t.Fatalf("unmarshal report: %v", err)
	}
	var buf strings.Builder
	n, err := emit(&parsed, zones, newFileLines(), cwd, machine, &buf)
	if err != nil {
		t.Fatalf("emit: %v", err)
	}
	return buf.String(), n
}

func broadZone(startLine, endLine int) callableZone {
	return callableZone{
		Start: zonePoint{Line: startLine - 1, Column: 0},
		End:   zonePoint{Line: endLine - 1, Column: 1000},
	}
}

func mustReport(t *testing.T, raw []byte) jscpdReport {
	t.Helper()
	var report jscpdReport
	if err := json.Unmarshal(raw, &report); err != nil {
		t.Fatal(err)
	}
	return report
}

func mustSingleFinding(t *testing.T, dir string, raw []byte, zones callableZones, rule string) wrapcli.Finding {
	t.Helper()
	out, n := emitZonesTo(t, dir, raw, zones, true)
	findings := parseNDJSON(t, out)
	if n != 1 || len(findings) != 1 || findings[0].Rule != rule {
		t.Fatalf("want one %s, got n=%d findings=%+v", rule, n, findings)
	}
	return findings[0]
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
		if !strings.Contains(f.Message, "clone of") {
			t.Fatalf("finding %+v: message must name the clone counterpart", f)
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

func TestHumanSummaryNamesSuppressedAndRemainingIntraFileEndpoints(t *testing.T) {
	dir := t.TempDir()
	p := writeSrc(t, dir, "clone.go", 5, []string{"// dup-ok: fixture proves suppression"})
	out, n := emitTo(t, dir, mkReport(p, 5, p, 8), false)
	if n != 1 {
		t.Fatalf("expected 1 surviving endpoint, got %d\n%s", n, out)
	}
	if !strings.Contains(out, "dup-ok suppressed: clone.go:5-8") {
		t.Fatalf("human summary must name the suppressed endpoint: %q", out)
	}
	if !strings.Contains(out, "remaining: clone.go:8-11") {
		t.Fatalf("human summary must name the remaining endpoint: %q", out)
	}
}

func TestDupOkBothEndpointsSuppressedClean(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.go", 5, []string{"// dup-ok: shared header block"})
	b := writeSrc(t, dir, "b.go", 8, []string{"// dup-ok: shared footer block"})
	for _, machine := range []bool{false, true} {
		out, n := emitTo(t, dir, mkReport(a, 5, b, 8), machine)
		if n != 0 {
			t.Fatalf("machine=%t: expected clean (0 survivors), got %d\n%s", machine, n, out)
		}
		if strings.TrimSpace(out) != "" {
			t.Fatalf("machine=%t: expected no output, got %q", machine, out)
		}
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

// D009: a reason under 10 chars is too cheap and does not suppress; a 10-char
// reason does.
func TestDupOkShortReasonDoesNotSuppress(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.go", 5, []string{"// dup-ok: too short"}) // 9 chars
	b := writeSrc(t, dir, "b.go", 8, nil)
	_, n := emitTo(t, dir, mkReport(a, 5, b, 8), true)
	if n != 2 {
		t.Fatalf("short-reason dup-ok must not suppress; got %d survivors, want 2", n)
	}
}

func TestDupOkTenCharReasonSuppresses(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.go", 5, []string{"// dup-ok: shared-css"}) // 10 chars
	b := writeSrc(t, dir, "b.go", 8, nil)
	_, n := emitTo(t, dir, mkReport(a, 5, b, 8), true)
	if n != 1 {
		t.Fatalf("10-char dup-ok must suppress one endpoint; got %d survivors, want 1", n)
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

func TestDupOkCssBlockComment(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.css", 5, []string{"/* dup-ok: shared grid, extraction tracked */"})
	b := writeSrc(t, dir, "b.css", 8, nil)
	_, n := emitTo(t, dir, mkSpanReport("css", a, 5, 8, b, 8, 11), true)
	if n != 1 {
		t.Fatalf("expected 1 survivor after /* dup-ok */, got %d", n)
	}
}

func TestDupOkMultiLineBlockCommentNotStandalone(t *testing.T) {
	dir := t.TempDir()
	// Only a single-line /* ... */ counts; an unterminated opener is not a
	// whole-line comment and must not suppress.
	a := writeSrc(t, dir, "a.css", 6, []string{"/* dup-ok: reason", "spanning two lines */"})
	b := writeSrc(t, dir, "b.css", 8, nil)
	_, n := emitTo(t, dir, mkSpanReport("css", a, 6, 9, b, 8, 11), true)
	if n != 2 {
		t.Fatalf("expected 2 survivors for multi-line block comment, got %d", n)
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

// jscpd names SFC sub-blocks as virtual files (`X.svelte:css`) with real-file
// line numbers; readReport must resolve them so dup-ok lookup reads the disk.
func TestReadReportResolvesVirtualSFCNames(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "A.svelte", 5, []string{"// dup-ok: shared palette, extraction tracked"})
	b := writeSrc(t, dir, "B.svelte", 5, nil)
	repFile := filepath.Join(t.TempDir(), "jscpd-report.json")
	rep0 := mkSpanReport("css", a+":css", 5, 8, b+":css", 5, 8)
	if err := os.WriteFile(repFile, rep0, 0o644); err != nil {
		t.Fatal(err)
	}
	rep, err := readReport(repFile)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Duplicates[0].FirstFile.Name != a || rep.Duplicates[0].SecondFile.Name != b {
		t.Fatalf("virtual names not resolved to real files: %+v", rep.Duplicates[0])
	}
	var buf strings.Builder
	if _, err := emit(rep, nil, newFileLines(), dir, false, &buf); err != nil {
		t.Fatalf("emit over resolved names: %v", err)
	}
	if !strings.Contains(buf.String(), "A.svelte") || strings.Contains(buf.String(), ":css") {
		t.Fatalf("output should carry the real path: %q", buf.String())
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

func TestReadReportMissingOrNullDuplicatesIsError(t *testing.T) {
	for _, raw := range []string{`{}`, `{"duplicates":null}`} {
		p := filepath.Join(t.TempDir(), "jscpd-report.json")
		if err := os.WriteFile(p, []byte(raw), 0o644); err != nil {
			t.Fatal(err)
		}
		if _, err := readReport(p); err == nil {
			t.Fatalf("raw report %s must fail loudly", raw)
		}
	}
}

func TestSvelteRawFixtureMapsVirtualNamesToPhysicalFiles(t *testing.T) {
	raw, err := os.ReadFile("testdata/svelte-virtual-report.json")
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	a := filepath.Join(dir, "A.svelte")
	b := filepath.Join(dir, "B.svelte")
	if err := os.WriteFile(a, []byte("<script>const a = () => 1;</script>\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(b, []byte("<script>const b = () => 1;</script>\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	raw = bytes.ReplaceAll(raw, []byte(`src/A.svelte`), []byte(a))
	raw = bytes.ReplaceAll(raw, []byte(`src/B.svelte`), []byte(b))
	report := filepath.Join(t.TempDir(), "jscpd-report.json")
	if err := os.WriteFile(report, raw, 0o644); err != nil {
		t.Fatal(err)
	}
	rep, err := readReport(report)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Duplicates[0].FirstFile.Name != a || rep.Duplicates[0].SecondFile.Name != b {
		t.Fatalf("virtual Svelte endpoints were not physicalized: %+v", rep.Duplicates[0])
	}
	zones := callableZones{
		"A.svelte": {broadZone(3, 10)},
		"B.svelte": {broadZone(4, 11)},
	}
	var out strings.Builder
	n, err := emit(rep, zones, newFileLines(), dir, true, &out)
	if err != nil || n != 0 || out.Len() != 0 {
		t.Fatalf("physical Svelte zones must filter the pair: n=%d err=%v out=%q", n, err, out.String())
	}
}

func TestVastaiHeaderFixtureUsesCanonicalJscpdCoordinatesAndDrops(t *testing.T) {
	raw, err := os.ReadFile("testdata/vastai-header-report.json")
	if err != nil {
		t.Fatal(err)
	}
	var rep jscpdReport
	if err := json.Unmarshal(raw, &rep); err != nil {
		t.Fatal(err)
	}
	if len(rep.Duplicates) != 1 {
		t.Fatalf("fixture duplicates = %d, want 1", len(rep.Duplicates))
	}
	c := &rep.Duplicates[0]
	if *c.FirstFile.StartLoc.Line != 874 || *c.FirstFile.StartLoc.Column != 34 ||
		*c.FirstFile.EndLoc.Line != 881 || *c.FirstFile.EndLoc.Column != 35 {
		t.Fatalf("fixture lost exact first endpoint coordinates: %+v", c.FirstFile)
	}

	dir := t.TempDir()
	source := writeSrc(t, dir, "tools.py", 2, nil)
	c.FirstFile.Name = source
	c.SecondFile.Name = source
	zones := callableZones{
		"tools.py": {
			broadZone(872, 887),
			broadZone(926, 941),
		},
	}
	var out strings.Builder
	n, err := emit(&rep, zones, newFileLines(), dir, true, &out)
	if err != nil {
		t.Fatal(err)
	}
	if n != 0 || out.Len() != 0 {
		t.Fatalf("both header-contained endpoints must be silent: n=%d out=%q", n, out.String())
	}
}

func TestCallableHeaderPairRequiresBothCompleteEndpointsContained(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.go", 5, nil)
	b := writeSrc(t, dir, "b.go", 8, nil)
	report := mkReport(a, 5, b, 8)

	cases := []struct {
		name  string
		zones callableZones
		want  int
	}{
		{
			name: "both contained",
			zones: callableZones{
				"a.go": {broadZone(4, 10)},
				"b.go": {broadZone(7, 13)},
			},
			want: 0,
		},
		{
			name: "only first contained",
			zones: callableZones{
				"a.go": {broadZone(4, 10)},
			},
			want: 2,
		},
		{
			name: "first body token crosses zone",
			zones: callableZones{
				"a.go": {broadZone(4, 7)},
				"b.go": {broadZone(7, 13)},
			},
			want: 2,
		},
		{
			name:  "empty zone lists",
			zones: callableZones{"a.go": {}, "b.go": {}},
			want:  2,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, got := emitZonesTo(t, dir, report, tc.zones, true)
			if got != tc.want {
				t.Fatalf("surviving findings = %d, want %d", got, tc.want)
			}
		})
	}
}

func TestCallableHeaderPathsUseCanonicalPhysicalIdentity(t *testing.T) {
	t.Run("in-repo dot-dot prefix is not parent traversal", func(t *testing.T) {
		dir := t.TempDir()
		a, b := writeSrcPair(t, dir, "..headers")
		zones := callableZones{
			"..headers/a.go": {broadZone(4, 10)},
			"..headers/b.go": {broadZone(7, 13)},
		}
		out, n := emitZonesTo(t, dir, mkReport(a, 5, b, 8), zones, true)
		if n != 0 || out != "" {
			t.Fatalf("contained endpoints under ..headers must be silent: n=%d out=%q", n, out)
		}
	})

	t.Run("symlink alias meets resolved sidecar key", func(t *testing.T) {
		dir := t.TempDir()
		a, b := writeSrcPair(t, dir, "physical")
		aliasDir := filepath.Join(dir, "alias")
		if err := os.Symlink(filepath.Dir(a), aliasDir); err != nil {
			t.Skipf("symlinks unavailable: %v", err)
		}
		aliasA := filepath.Join(aliasDir, filepath.Base(a))
		aliasB := filepath.Join(aliasDir, filepath.Base(b))
		zones := callableZones{
			"physical/a.go": {broadZone(4, 10)},
			"physical/b.go": {broadZone(7, 13)},
		}
		out, n := emitZonesTo(t, dir, mkReport(aliasA, 5, aliasB, 8), zones, true)
		if n != 0 || out != "" {
			t.Fatalf("symlink endpoints must use physical sidecar keys: n=%d out=%q", n, out)
		}
	})
}

func TestIncompleteEmptyOrInvertedEndpointCoordinatesStayDUP001(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.go", 5, nil)
	b := writeSrc(t, dir, "b.go", 8, nil)
	rep := mustReport(t, mkReport(a, 5, b, 8))
	zones := callableZones{
		"a.go": {broadZone(4, 20)},
		"b.go": {broadZone(7, 20)},
	}

	rep.Duplicates[0].FirstFile.StartLoc.Column = nil
	var out strings.Builder
	n, err := emit(&rep, zones, newFileLines(), dir, true, &out)
	if err != nil || n != 2 {
		t.Fatalf("partial coordinates must stay red: n=%d err=%v out=%q", n, err, out.String())
	}

	empty := mustReport(t, mkReport(a, 5, b, 8))
	*empty.Duplicates[0].FirstFile.EndLoc.Line = *empty.Duplicates[0].FirstFile.StartLoc.Line
	*empty.Duplicates[0].FirstFile.EndLoc.Column = *empty.Duplicates[0].FirstFile.StartLoc.Column
	out.Reset()
	n, err = emit(&empty, zones, newFileLines(), dir, true, &out)
	if err != nil || n != 2 {
		t.Fatalf("empty coordinates must stay red: n=%d err=%v out=%q", n, err, out.String())
	}

	inverted := mustReport(t, mkReport(a, 5, b, 8))
	line := 4
	inverted.Duplicates[0].FirstFile.EndLoc.Line = &line
	out.Reset()
	n, err = emit(&inverted, zones, newFileLines(), dir, true, &out)
	if err != nil || n != 2 {
		t.Fatalf("inverted coordinates must stay red: n=%d err=%v out=%q", n, err, out.String())
	}
}

func TestMalformedExplicitCallableZonesFailLoudly(t *testing.T) {
	cases := []string{
		`{`,
		`{}`,
		`{"files":{"a.py":[{"start":{"line":0},"end":{"line":1,"column":0}}]}}`,
		`{"files":{"a.py":[{"start":{"line":0,"column":0},"end":{"column":1}}]}}`,
		`{"files":{"a.py":[{"start":{"line":0,"column":0},"end":{"line":0,"column":0}}]}}`,
		`{"files":{"a.py":[{"start":{"line":1,"column":0},"end":{"line":0,"column":1}}]}}`,
		`{"files":{},"extra":true}`,
		`{"files":{}} trailing`,
	}
	for _, raw := range cases {
		path := filepath.Join(t.TempDir(), "zones.json")
		if err := os.WriteFile(path, []byte(raw), 0o644); err != nil {
			t.Fatal(err)
		}
		if _, err := readCallableZones(path); err == nil {
			t.Fatalf("malformed sidecar passed: %s", raw)
		}
	}
}

func TestCloneReportRequiresExplicitCallableZones(t *testing.T) {
	repo := t.TempDir()
	a := writeSrc(t, repo, "a.go", 5, nil)
	b := writeSrc(t, repo, "b.go", 8, nil)
	bin := buildWrapper(t)
	report := filepath.Join(t.TempDir(), "report.json")
	if err := os.WriteFile(report, mkReport(a, 5, b, 8), 0o644); err != nil {
		t.Fatal(err)
	}
	_, stderr, err := runWith(t, bin, repo, nil, "--report", report, "a.go", "b.go")
	if err == nil || !strings.Contains(stderr, "--callable-zones was not provided") {
		t.Fatalf("clone report without sidecar must fail loudly: err=%v stderr=%q", err, stderr)
	}
}

func TestEmptyReportNeedsNoCallableZones(t *testing.T) {
	repo := t.TempDir()
	writeSrc(t, repo, "a.go", 5, nil)
	bin := buildWrapper(t)
	report := filepath.Join(t.TempDir(), "report.json")
	if err := os.WriteFile(report, []byte(`{"duplicates":[]}`), 0o644); err != nil {
		t.Fatal(err)
	}
	out, stderr, err := runWith(t, bin, repo, nil, "--report", report, "a.go")
	if err != nil || out != "" || stderr != "" {
		t.Fatalf("empty report without sidecar must be clean: err=%v out=%q stderr=%q", err, out, stderr)
	}
}

func TestRedundantMarkerEmitsOneDUP003(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.go", 5, []string{"// dup-ok: shared callable contract"})
	b := writeSrc(t, dir, "b.go", 8, nil)
	zones := callableZones{
		"a.go": {broadZone(4, 10)},
		"b.go": {broadZone(7, 13)},
	}
	finding := mustSingleFinding(t, dir, mkReport(a, 5, b, 8), zones, "DUP003")
	if finding.File != "a.go" || finding.Line != 4 {
		t.Fatalf("want DUP003 at marker, got %+v", finding)
	}
	if !strings.Contains(finding.Message, "remove the marker and matching approval together") {
		t.Fatalf("DUP003 lacks cleanup action: %+v", finding)
	}
}

func TestMarkerSharedWithSurvivingEndpointIsNotDUP003(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.go", 5, []string{"// dup-ok: shared callable contract"})
	b := writeSrc(t, dir, "b.go", 8, nil)
	c := writeSrc(t, dir, "c.go", 8, nil)
	auto := mustReport(t, mkReport(a, 5, b, 8))
	survivor := mustReport(t, mkReport(a, 5, c, 8))
	auto.Duplicates = append(auto.Duplicates, survivor.Duplicates...)
	raw, _ := json.Marshal(auto)
	zones := callableZones{
		"a.go": {broadZone(4, 10)},
		"b.go": {broadZone(7, 13)},
	}
	finding := mustSingleFinding(t, dir, raw, zones, "DUP001")
	if finding.File != "c.go" {
		t.Fatalf("shared marker must stay valid for survivor: %+v", finding)
	}
}

func TestMarkerSeenOnMultipleAutoDroppedEndpointsEmitsOneDUP003(t *testing.T) {
	dir := t.TempDir()
	a := writeSrc(t, dir, "a.go", 5, []string{"// dup-ok: shared callable contract"})
	b := writeSrc(t, dir, "b.go", 8, nil)
	rep := mustReport(t, mkReport(a, 5, b, 8))
	rep.Duplicates = append(rep.Duplicates, rep.Duplicates[0])
	raw, _ := json.Marshal(rep)
	zones := callableZones{
		"a.go": {broadZone(4, 10)},
		"b.go": {broadZone(7, 13)},
	}
	mustSingleFinding(t, dir, raw, zones, "DUP003")
}

func TestRelToIsRepoRelativePosixInBothBranches(t *testing.T) {
	dir := t.TempDir()
	if got := relTo(dir, filepath.Join(dir, "sub", "deep", "a.go")); got != "sub/deep/a.go" {
		t.Fatalf("in-repo relTo = %q, want sub/deep/a.go", got)
	}
	// Outside cwd the name is returned verbatim; that branch is POSIX too.
	outside := filepath.Join(filepath.Dir(dir), "outside", "b.go")
	if got := relTo(dir, outside); got != filepath.ToSlash(outside) {
		t.Fatalf("out-of-repo relTo = %q, want %q", got, filepath.ToSlash(outside))
	}
}

// assertSubEndpoints pins the machine findings of a writeSrcPair clone to their
// repo-relative POSIX paths.
func assertSubEndpoints(t *testing.T, out string) {
	t.Helper()
	want := map[string]int{"sub/a.go": 5, "sub/b.go": 8}
	for _, f := range parseNDJSON(t, out) {
		if want[f.File] != f.Line {
			t.Fatalf("finding %+v not in expected %v", f, want)
		}
	}
}

// The machine contract is repo-relative POSIX: the hook compares the File field
// against an as_posix() path, so a Windows `\` misfiles the finding silently.
func TestMachineFindingFilesUsePosixSeparators(t *testing.T) {
	t.Run("DUP001 clone endpoints", func(t *testing.T) {
		dir := t.TempDir()
		a, b := writeSrcPair(t, dir, "sub")
		out, n := emitTo(t, dir, mkReport(a, 5, b, 8), true)
		if n != 2 {
			t.Fatalf("expected 2 surviving endpoints, got %d\n%s", n, out)
		}
		assertSubEndpoints(t, out)
	})

	t.Run("DUP002 native ignore marker", func(t *testing.T) {
		dir := t.TempDir()
		p := filepath.Join(mkSubdir(t, dir, "sub"), "a.go")
		src := "package x\n// " + ignoreMarker + "-start\nfunc A() int { return 1 }\n"
		if err := os.WriteFile(p, []byte(src), 0o644); err != nil {
			t.Fatal(err)
		}
		var buf strings.Builder
		n, err := emitIgnoreBans(newFileLines(), []string{p}, dir, true, &buf)
		if err != nil || n != 1 {
			t.Fatalf("emitIgnoreBans: n=%d err=%v", n, err)
		}
		fs := parseNDJSON(t, buf.String())
		if len(fs) != 1 || fs[0].File != "sub/a.go" || fs[0].Line != 2 {
			t.Fatalf("want DUP002 at sub/a.go:2, got %+v", fs)
		}
	})

	t.Run("DUP003 redundant marker", func(t *testing.T) {
		dir := t.TempDir()
		sub := mkSubdir(t, dir, "sub")
		a := writeSrc(t, sub, "a.go", 5, []string{"// dup-ok: shared callable contract"})
		b := writeSrc(t, sub, "b.go", 8, nil)
		zones := callableZones{
			"sub/a.go": {broadZone(4, 10)},
			"sub/b.go": {broadZone(7, 13)},
		}
		finding := mustSingleFinding(t, dir, mkReport(a, 5, b, 8), zones, "DUP003")
		if finding.File != "sub/a.go" || finding.Line != 4 {
			t.Fatalf("want DUP003 at sub/a.go:4, got %+v", finding)
		}
	})
}

// jscpd 5.0.12 names every Windows endpoint in extended-length form
// (\\?\C:\...); filepath.Rel cannot relativize that against the plain cwd, so
// the whole absolute path would ride into the machine finding.
func TestExtendedLengthEndpointNamesBecomeRepoRelative(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("extended-length prefixes are a windows path spelling")
	}
	dir := t.TempDir()
	a, b := writeSrcPair(t, dir, "sub")
	repFile := filepath.Join(t.TempDir(), "jscpd-report.json")
	if err := os.WriteFile(repFile, mkReport(`\\?\`+a, 5, `\\?\`+b, 8), 0o644); err != nil {
		t.Fatal(err)
	}
	rep, err := readReport(repFile)
	if err != nil {
		t.Fatal(err)
	}
	var buf strings.Builder
	n, err := emit(rep, nil, newFileLines(), dir, true, &buf)
	if err != nil || n != 2 {
		t.Fatalf("emit over extended-length names: n=%d err=%v out=%q", n, err, buf.String())
	}
	assertSubEndpoints(t, buf.String())
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

// runReport builds the post-processor and gives it an explicit raw report plus
// a syntactically valid empty zone sidecar.
func runReport(t *testing.T, repo string, report []byte, files ...string) (string, string, error) {
	t.Helper()
	bin := buildWrapper(t)
	repFile := filepath.Join(t.TempDir(), "rep.json")
	if err := os.WriteFile(repFile, report, 0o644); err != nil {
		t.Fatal(err)
	}
	zonesFile := filepath.Join(t.TempDir(), "zones.json")
	if err := os.WriteFile(zonesFile, []byte(`{"files":{}}`), 0o644); err != nil {
		t.Fatal(err)
	}
	args := []string{"--report", repFile, "--callable-zones", zonesFile, "--machine"}
	return runWith(t, bin, repo, nil, append(args, files...)...)
}

func TestRunReportHappyPath(t *testing.T) {
	repo := t.TempDir()
	a := writeSrc(t, repo, "a.go", 5, nil)
	b := writeSrc(t, repo, "b.go", 8, nil)
	out, errOut, runErr := runReport(t, repo, mkReport(a, 5, b, 8), "a.go", "b.go")
	if runErr == nil {
		t.Fatalf("expected exit 1 for surviving clones; got clean\nstdout=%s\nstderr=%s", out, errOut)
	}
	fs := parseNDJSON(t, out)
	if len(fs) != 2 {
		t.Fatalf("expected 2 findings through run(), got %d: %s", len(fs), out)
	}
}

func TestRunUnparseableReportNeverClean(t *testing.T) {
	repo := t.TempDir()
	writeSrc(t, repo, "a.go", 5, nil)
	out, errOut, runErr := runReport(t, repo, []byte("{garbage"), "a.go")
	if runErr == nil {
		t.Fatalf("unparseable report must be nonzero, never clean\nstdout=%s", out)
	}
	if !strings.Contains(errOut, "parse jscpd report") {
		t.Fatalf("stderr should name the parse failure: %s", errOut)
	}
}

func TestRunMissingReportNeverClean(t *testing.T) {
	bin := buildWrapper(t)
	repo := t.TempDir()
	writeSrc(t, repo, "a.go", 5, nil)
	out, errOut, runErr := runWith(t, bin, repo, nil,
		"--report", filepath.Join(repo, "nonexistent-report"), "--machine", "a.go")
	if runErr == nil {
		t.Fatalf("a missing raw report must exit nonzero, never clean\nstdout=%s", out)
	}
	if !strings.Contains(errOut, "read jscpd report") {
		t.Fatalf("stderr should name the report read failure: %s", errOut)
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
	out, errOut, runErr := runReport(t, repo, []byte(`{"duplicates": []}`), "a.go")
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

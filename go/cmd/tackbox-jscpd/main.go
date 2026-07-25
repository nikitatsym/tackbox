// Command tackbox-jscpd wraps the vendored jscpd copy/paste detector into the
// tackbox engine contract: post-process one raw jscpd JSON report, drop Java
// file-header and callable-header clone pairs, apply dup-ok suppression, ban
// native jscpd ignore markers (DUP002), and emit findings.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/nikitatsym/tackbox/go/internal/wrapcli"
)

// version is injected at build time via -ldflags "-X main.version=...".
var version = "dev"

const (
	ruleID          = "DUP001"
	banRuleID       = "DUP002"
	redundantRuleID = "DUP003"
	minReason       = 10 // D009: a dup-ok reason must be at least this many chars after trimming
)

// ignoreMarker is jscpd's native suppression prefix (ignore-start/-end). Built
// by concatenation so this source never contains the substring it bans.
const ignoreMarker = "jscpd:" + "ignore"

func main() {
	wrapcli.Main("tackbox-jscpd", version, run)
}

func run(args []string, stdout, stderr io.Writer) (int, error) {
	machine, reportPath, zonesPath, files, err := parseArgs(args)
	if err != nil {
		return 0, err
	}
	if reportPath == "" {
		return 0, errors.New("missing --report <path> (raw jscpd JSON report)")
	}
	cwd, err := os.Getwd()
	if err != nil {
		return 0, fmt.Errorf("get cwd: %w", err)
	}
	absFiles := wrapcli.ToAbs(cwd, files)
	rep, err := readReport(reportPath)
	if err != nil {
		return 0, err
	}
	if len(rep.Duplicates) > 0 && zonesPath == "" {
		return 0, errors.New("raw jscpd report has clone endpoints but --callable-zones was not provided")
	}
	zones := callableZones{}
	if zonesPath != "" {
		zones, err = readCallableZones(zonesPath)
		if err != nil {
			return 0, err
		}
	}
	fl := newFileLines()
	surviving, err := emit(rep, zones, fl, cwd, machine, stdout)
	if err != nil {
		return 0, err
	}
	banned, err := emitIgnoreBans(fl, absFiles, cwd, machine, stdout)
	if err != nil {
		return 0, err
	}
	if surviving+banned > 0 {
		return 1, nil
	}
	return 0, nil
}

// fileLines caches file contents split into lines; every source read in one run
// (header classification, dup-ok lookup, ignore-marker scan) goes through it.
type fileLines struct {
	cache map[string][]string
}

func newFileLines() *fileLines {
	return &fileLines{cache: map[string][]string{}}
}

func (fl *fileLines) get(path string) ([]string, error) {
	if lines, ok := fl.cache[path]; ok {
		return lines, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	lines := strings.Split(string(data), "\n")
	fl.cache[path] = lines
	return lines, nil
}

func parseArgs(args []string) (machine bool, reportPath, zonesPath string, files []string, err error) {
	for i := 0; i < len(args); i++ {
		a := args[i]
		switch {
		case a == "--machine":
			machine = true
		case a == "--report":
			if i+1 >= len(args) {
				return false, "", "", nil, errors.New("--report requires a path argument")
			}
			i++
			reportPath = args[i]
		case strings.HasPrefix(a, "--report="):
			reportPath = strings.TrimPrefix(a, "--report=")
		case a == "--callable-zones":
			if i+1 >= len(args) {
				return false, "", "", nil, errors.New("--callable-zones requires a path argument")
			}
			i++
			zonesPath = args[i]
		case strings.HasPrefix(a, "--callable-zones="):
			zonesPath = strings.TrimPrefix(a, "--callable-zones=")
		case a == "--paths-from":
			if i+1 >= len(args) {
				return false, "", "", nil, errors.New("--paths-from requires a path argument")
			}
			i++
			// The file set rides a list-file, not positional argv (ARG_MAX safety).
			paths, rerr := wrapcli.ReadPathList(args[i])
			if rerr != nil {
				return false, "", "", nil, rerr
			}
			files = append(files, paths...)
		default:
			files = append(files, a)
		}
	}
	return machine, reportPath, zonesPath, files, nil
}

type sourcePoint struct {
	Line   *int `json:"line"`
	Column *int `json:"column"`
}

type endpoint struct {
	Name     string      `json:"name"`
	Start    int         `json:"start"`
	End      int         `json:"end"`
	StartLoc sourcePoint `json:"startLoc"`
	EndLoc   sourcePoint `json:"endLoc"`
}

// lineNo is the endpoint's reported line: startLoc.line when jscpd supplied it,
// else the flat start field (the two agree in 5.0.12 output).
func (e endpoint) lineNo() int {
	if e.StartLoc.Line != nil && *e.StartLoc.Line > 0 {
		return *e.StartLoc.Line
	}
	return e.Start
}

func (e endpoint) endLineNo() int {
	if e.EndLoc.Line != nil && *e.EndLoc.Line > 0 {
		return *e.EndLoc.Line
	}
	return e.End
}

type clone struct {
	FirstFile  endpoint `json:"firstFile"`
	SecondFile endpoint `json:"secondFile"`
	Format     string   `json:"format"`
	Tokens     int      `json:"tokens"`
}

type jscpdReport struct {
	Duplicates []clone `json:"duplicates"`
}

type zonePoint struct {
	Line   int `json:"line"`
	Column int `json:"column"`
}

type callableZone struct {
	Start zonePoint
	End   zonePoint
}

type rawZonePoint struct {
	Line   *int `json:"line"`
	Column *int `json:"column"`
}

type rawCallableZone struct {
	Start rawZonePoint `json:"start"`
	End   rawZonePoint `json:"end"`
}

type callableZoneDocument struct {
	Files map[string][]rawCallableZone `json:"files"`
}

type callableZones map[string][]callableZone

func readCallableZones(path string) (callableZones, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read callable zones %s: %w", path, err)
	}
	var doc callableZoneDocument
	dec := json.NewDecoder(strings.NewReader(string(data)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&doc); err != nil {
		return nil, fmt.Errorf("parse callable zones %s: %w", path, err)
	}
	if doc.Files == nil {
		return nil, fmt.Errorf("parse callable zones %s: missing files object", path)
	}
	out := callableZones{}
	for file, zones := range doc.Files {
		if file == "" {
			return nil, fmt.Errorf("parse callable zones %s: empty file path", path)
		}
		out[file] = []callableZone{}
		for i, raw := range zones {
			if raw.Start.Line == nil || raw.Start.Column == nil ||
				raw.End.Line == nil || raw.End.Column == nil {
				return nil, fmt.Errorf("parse callable zones %s: incomplete zone %s[%d]", path, file, i)
			}
			zone := callableZone{
				Start: zonePoint{Line: *raw.Start.Line, Column: *raw.Start.Column},
				End:   zonePoint{Line: *raw.End.Line, Column: *raw.End.Column},
			}
			if !validZonePoint(zone.Start) || !validZonePoint(zone.End) ||
				!pointLess(zone.Start, zone.End) {
				return nil, fmt.Errorf("parse callable zones %s: invalid zone %s[%d]", path, file, i)
			}
			out[file] = append(out[file], zone)
		}
	}
	if err := dec.Decode(&struct{}{}); err != io.EOF {
		return nil, fmt.Errorf("parse callable zones %s: trailing JSON data", path)
	}
	return out, nil
}

func validZonePoint(p zonePoint) bool {
	return p.Line >= 0 && p.Column >= 0
}

func pointLess(a, b zonePoint) bool {
	return a.Line < b.Line || (a.Line == b.Line && a.Column < b.Column)
}

func readReport(path string) (*jscpdReport, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read jscpd report %s: %w", path, err)
	}
	var envelope struct {
		Duplicates json.RawMessage `json:"duplicates"`
	}
	if err := json.Unmarshal(data, &envelope); err != nil {
		return nil, fmt.Errorf("parse jscpd report %s: %w", path, err)
	}
	duplicates := strings.TrimSpace(string(envelope.Duplicates))
	if !strings.HasPrefix(duplicates, "[") {
		return nil, fmt.Errorf("parse jscpd report %s: missing duplicates array", path)
	}
	var rep jscpdReport
	if err := json.Unmarshal(envelope.Duplicates, &rep.Duplicates); err != nil {
		return nil, fmt.Errorf("parse jscpd report %s duplicates: %w", path, err)
	}
	for i := range rep.Duplicates {
		rep.Duplicates[i].FirstFile.Name = realPath(rep.Duplicates[i].FirstFile.Name)
		rep.Duplicates[i].SecondFile.Name = realPath(rep.Duplicates[i].SecondFile.Name)
	}
	return &rep, nil
}

// realPath resolves jscpd's virtual SFC sub-block names (`X.svelte:css`) to
// the on-disk file; their line numbers are already real-file coordinates.
func realPath(name string) string {
	if _, err := os.Stat(name); err == nil {
		return name
	}
	if i := strings.LastIndex(name, ":"); i > 0 {
		if base := name[:i]; base != "" {
			if _, err := os.Stat(base); err == nil {
				return base
			}
		}
	}
	return name
}

type markerKey struct {
	File string
	Line int
}

type markerUsage struct {
	autoDropped bool
	surviving   bool
}

// emit classifies complete pairs before endpoint-level marker suppression.
// Marker usage is accumulated over the complete report so DUP003 is emitted
// only for a marker used by auto-dropped endpoints and no surviving endpoint.
func emit(rep *jscpdReport, zones callableZones, fl *fileLines, cwd string, machine bool, w io.Writer) (int, error) {
	enc := json.NewEncoder(w)
	surviving := 0
	uses := map[markerKey]*markerUsage{}
	for _, c := range rep.Duplicates {
		headerOnly, err := javaHeaderClone(c, fl)
		if err != nil {
			return 0, err
		}
		if headerOnly {
			continue
		}
		aMarker, aSup, err := suppressionCandidate(fl, c.FirstFile)
		if err != nil {
			return 0, err
		}
		bMarker, bSup, err := suppressionCandidate(fl, c.SecondFile)
		if err != nil {
			return 0, err
		}
		autoDropped := callableHeaderClone(c, zones, cwd)
		for key, ok := range map[markerKey]bool{aMarker: aSup, bMarker: bSup} {
			if !ok {
				continue
			}
			usage := uses[key]
			if usage == nil {
				usage = &markerUsage{}
				uses[key] = usage
			}
			if autoDropped {
				usage.autoDropped = true
			} else {
				usage.surviving = true
			}
		}
		if autoDropped {
			continue
		}
		aRel := relTo(cwd, c.FirstFile.Name)
		bRel := relTo(cwd, c.SecondFile.Name)
		if machine {
			if !aSup {
				msg := fmt.Sprintf("duplicated block, clone of %s:%d-%d (%d tokens); extract the shared code",
					bRel, c.SecondFile.Start, c.SecondFile.End, c.Tokens)
				if err := enc.Encode(wrapcli.Finding{File: aRel, Line: c.FirstFile.lineNo(), Rule: ruleID, Message: msg}); err != nil {
					return 0, err
				}
				surviving++
			}
			if !bSup {
				msg := fmt.Sprintf("duplicated block, clone of %s:%d-%d (%d tokens); extract the shared code",
					aRel, c.FirstFile.Start, c.FirstFile.End, c.Tokens)
				if err := enc.Encode(wrapcli.Finding{File: bRel, Line: c.SecondFile.lineNo(), Rule: ruleID, Message: msg}); err != nil {
					return 0, err
				}
				surviving++
			}
			continue
		}
		n := 0
		if !aSup {
			n++
		}
		if !bSup {
			n++
		}
		if n == 0 {
			continue
		}
		line := fmt.Sprintf("%s %s:%d-%d <-> %s:%d-%d (%d tokens)",
			ruleID, aRel, c.FirstFile.Start, c.FirstFile.End,
			bRel, c.SecondFile.Start, c.SecondFile.End, c.Tokens)
		var sup []string
		var remaining []string
		if aSup {
			sup = append(sup, fmt.Sprintf("%s:%d-%d", aRel, c.FirstFile.Start, c.FirstFile.End))
		} else {
			remaining = append(remaining, fmt.Sprintf("%s:%d-%d", aRel, c.FirstFile.Start, c.FirstFile.End))
		}
		if bSup {
			sup = append(sup, fmt.Sprintf("%s:%d-%d", bRel, c.SecondFile.Start, c.SecondFile.End))
		} else {
			remaining = append(remaining, fmt.Sprintf("%s:%d-%d", bRel, c.SecondFile.Start, c.SecondFile.End))
		}
		if len(sup) > 0 {
			line += " [dup-ok suppressed: " + strings.Join(sup, ", ") + "; remaining: " + strings.Join(remaining, ", ") + "]"
		}
		if _, err := fmt.Fprintln(w, line); err != nil {
			return 0, err
		}
		surviving += n
	}
	keys := make([]markerKey, 0, len(uses))
	for key, usage := range uses {
		if usage.autoDropped && !usage.surviving {
			keys = append(keys, key)
		}
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].File != keys[j].File {
			return keys[i].File < keys[j].File
		}
		return keys[i].Line < keys[j].Line
	})
	for _, key := range keys {
		rel := relTo(cwd, key.File)
		message := "dup-ok is unnecessary for an automatically filtered callable-header pair; remove the marker and matching approval together"
		if machine {
			if err := enc.Encode(wrapcli.Finding{
				File: rel, Line: key.Line, Rule: redundantRuleID, Message: message,
			}); err != nil {
				return 0, err
			}
		} else if _, err := fmt.Fprintf(w, "%s %s:%d %s\n", redundantRuleID, rel, key.Line, message); err != nil {
			return 0, err
		}
		surviving++
	}
	return surviving, nil
}

func callableHeaderClone(c clone, zones callableZones, cwd string) bool {
	return endpointInCallableHeader(c.FirstFile, zones, cwd) &&
		endpointInCallableHeader(c.SecondFile, zones, cwd)
}

func endpointInCallableHeader(e endpoint, zones callableZones, cwd string) bool {
	start, end, ok := endpointPoints(e)
	if !ok {
		return false
	}
	rel := filepath.ToSlash(physicalRelTo(cwd, e.Name))
	for _, zone := range zones[rel] {
		if !pointLess(start, zone.Start) && !pointLess(zone.End, end) {
			return true
		}
	}
	return false
}

// jscpd 5.0.12 locations use one-based lines, zero-based columns, and a
// half-open end point. The sidecar is zero-based and half-open.
func endpointPoints(e endpoint) (zonePoint, zonePoint, bool) {
	if e.StartLoc.Line == nil || e.StartLoc.Column == nil ||
		e.EndLoc.Line == nil || e.EndLoc.Column == nil {
		return zonePoint{}, zonePoint{}, false
	}
	if *e.StartLoc.Line < 1 || *e.EndLoc.Line < 1 ||
		*e.StartLoc.Column < 0 || *e.EndLoc.Column < 0 {
		return zonePoint{}, zonePoint{}, false
	}
	start := zonePoint{Line: *e.StartLoc.Line - 1, Column: *e.StartLoc.Column}
	end := zonePoint{Line: *e.EndLoc.Line - 1, Column: *e.EndLoc.Column}
	if !pointLess(start, end) {
		return zonePoint{}, zonePoint{}, false
	}
	return start, end, true
}

// physicalRelTo mirrors Python's resolved physical-path keys in the callable
// zone sidecar. jscpd can report a symlink alias even though zone extraction
// resolved it to the target; both spellings must meet at the same key.
func physicalRelTo(cwd, name string) string {
	root := filepath.Clean(cwd)
	if resolved, err := filepath.EvalSymlinks(cwd); err == nil {
		root = resolved
	}
	path := name
	if !filepath.IsAbs(path) {
		path = filepath.Join(cwd, path)
	}
	physical := filepath.Clean(path)
	if resolved, err := filepath.EvalSymlinks(path); err == nil {
		physical = resolved
	}
	return relTo(root, physical)
}

func relTo(cwd, name string) string {
	rel, err := filepath.Rel(cwd, name)
	if err != nil || filepath.IsAbs(rel) ||
		rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return name
	}
	return rel
}

// javaHeaderClone reports whether a format=java clone lies entirely within both
// files' headers (package + imports + leading comments). Such clones carry no
// extractable code, so they are dropped before suppression and never output.
// Java-only by pin; other languages need their own decision.
func javaHeaderClone(c clone, fl *fileLines) (bool, error) {
	if c.Format != "java" {
		return false, nil
	}
	for _, e := range [2]endpoint{c.FirstFile, c.SecondFile} {
		lines, err := fl.get(e.Name)
		if err != nil {
			return false, err
		}
		if e.lineNo() < 1 || e.endLineNo() > javaHeaderEnd(lines) {
			return false, nil
		}
	}
	return true, nil
}

// javaHeaderEnd returns the 1-based last line of the file's header: the leading
// run of blank lines, line and block comments (javadoc included), package and
// import (incl. import static) declarations. The first other line - a type
// declaration, an annotation, any code - ends the header; 0 when line 1 already
// is one. Line-based on purpose (the pin): no java parsing.
func javaHeaderEnd(lines []string) int {
	inBlock := false
	for i, raw := range lines {
		text := strings.TrimSpace(raw)
		if inBlock {
			idx := strings.Index(text, "*/")
			if idx < 0 {
				continue
			}
			inBlock = false
			if rest := strings.TrimSpace(text[idx+2:]); rest != "" {
				// Code after a block-comment close is not a header line.
				return i
			}
			continue
		}
		switch {
		case text == "",
			strings.HasPrefix(text, "//"),
			strings.HasPrefix(text, "package "),
			strings.HasPrefix(text, "import "):
		case strings.HasPrefix(text, "/*"):
			if idx := strings.Index(text[2:], "*/"); idx >= 0 {
				if rest := strings.TrimSpace(text[2+idx+2:]); rest != "" {
					return i
				}
			} else {
				inBlock = true
			}
		default:
			return i
		}
	}
	return len(lines)
}

// emitIgnoreBans reports every native jscpd ignore marker in the scanned files
// as a DUP002 finding: that suppression channel bypasses the dup-ok approval
// gate, so its presence is itself a defect. Substring scan by pin.
func emitIgnoreBans(fl *fileLines, absFiles []string, cwd string, machine bool, w io.Writer) (int, error) {
	enc := json.NewEncoder(w)
	found := 0
	for _, f := range absFiles {
		lines, err := fl.get(f)
		if err != nil {
			return 0, err
		}
		for i, line := range lines {
			if !strings.Contains(line, ignoreMarker) {
				continue
			}
			found++
			rel := relTo(cwd, f)
			if machine {
				// Machine messages reach the hook and must not spell marker
				// recipes; the human line keeps the migration hint.
				msg := fmt.Sprintf("native %s marker is banned: it bypasses the gated duplication-suppression channel; remove it",
					ignoreMarker)
				if err := enc.Encode(wrapcli.Finding{File: rel, Line: i + 1, Rule: banRuleID, Message: msg}); err != nil {
					return 0, err
				}
				continue
			}
			msg := fmt.Sprintf("%s %s:%d native %s marker is banned; use // dup-ok: <reason> above the endpoint",
				banRuleID, rel, i+1, ignoreMarker)
			if _, err := fmt.Fprintln(w, msg); err != nil {
				return 0, err
			}
		}
	}
	return found, nil
}

// suppressionCandidate returns the valid dup-ok marker in the standalone
// comment block directly above the endpoint, including its source line.
// Semantics mirror go/internal/markers.Above: the block's last line must be
// startLine-1, and the marker may sit on any line of that contiguous block.
// Only whole-line // and # comments count - a trailing comment after code is
// not a standalone block.
func suppressionCandidate(fl *fileLines, e endpoint) (markerKey, bool, error) {
	startLine := e.lineNo()
	if startLine < 2 {
		return markerKey{}, false, nil
	}
	lines, err := fl.get(e.Name)
	if err != nil {
		return markerKey{}, false, fmt.Errorf("dup-ok check: %w", err)
	}
	for ln := startLine - 1; ln >= 1; ln-- {
		if ln > len(lines) {
			return markerKey{}, false, nil
		}
		body, ok := commentBody(strings.TrimSpace(lines[ln-1]))
		if !ok {
			return markerKey{}, false, nil
		}
		if reason, ok := dupOkReason(body); ok && len(reason) >= minReason {
			return markerKey{File: e.Name, Line: ln}, true, nil
		}
	}
	return markerKey{}, false, nil
}

// commentBody strips a whole-line comment marker (`//`, `#`, or a single-line
// `/* ... */` - CSS has no line comments) and returns the trimmed remainder;
// ok is false when the line is not a whole-line comment.
func commentBody(text string) (string, bool) {
	switch {
	case strings.HasPrefix(text, "/*") && strings.HasSuffix(text, "*/") && len(text) >= 4:
		return strings.TrimSpace(text[2 : len(text)-2]), true
	case strings.HasPrefix(text, "//"):
		return strings.TrimSpace(text[2:]), true
	case strings.HasPrefix(text, "#"):
		return strings.TrimSpace(text[1:]), true
	}
	return "", false
}

func dupOkReason(body string) (string, bool) {
	const prefix = "dup-ok:"
	if !strings.HasPrefix(body, prefix) {
		return "", false
	}
	return strings.TrimSpace(body[len(prefix):]), true
}

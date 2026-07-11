// Command tackbox-jscpd wraps the vendored jscpd copy/paste detector into the
// tackbox engine contract: run jscpd with the JSON reporter, drop java clones
// confined to file headers, apply dup-ok suppression, ban native jscpd ignore
// markers (DUP002), and emit findings. The jscpd binary path is passed via
// --jscpd (dev fetch or hermetic store); jscpd's own exit code is ignored (it
// exits 0 with or without clones), so the wrapper decides the exit from the
// surviving findings. Any spawn failure or unreadable/unparseable report is a
// loud nonzero exit, never a silent clean.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/nikitatsym/tackbox/go/internal/wrapcli"
)

// version is injected at build time via -ldflags "-X main.version=...".
var version = "dev"

const (
	ruleID     = "DUP001"
	banRuleID  = "DUP002"
	minTokens  = "50" // pinned threshold; tuning lives in tackbox, not the binary
	reportName = "jscpd-report.json"
)

// ignoreMarker is jscpd's native suppression prefix (ignore-start/-end). Built
// by concatenation so this source never contains the substring it bans.
const ignoreMarker = "jscpd:" + "ignore"

func main() {
	wrapcli.Main("tackbox-jscpd", version, run)
}

func run(args []string, stdout, stderr io.Writer) (int, error) {
	machine, jscpdBin, files, err := parseArgs(args)
	if err != nil {
		return 0, err
	}
	if jscpdBin == "" {
		return 0, errors.New("missing --jscpd <path> (jscpd binary location)")
	}
	if len(files) == 0 {
		return 0, nil
	}
	cwd, err := os.Getwd()
	if err != nil {
		return 0, fmt.Errorf("get cwd: %w", err)
	}
	outDir, err := os.MkdirTemp("", "tackbox-jscpd-*")
	if err != nil {
		return 0, fmt.Errorf("create report dir: %w", err)
	}
	defer os.RemoveAll(outDir)

	// --absolute keeps report paths independent of the base jscpd would infer;
	// the wrapper relativizes them to cwd. --no-gitignore stops jscpd dropping
	// an explicitly-passed file (the caller's source set already excludes it).
	full := []string{
		"--min-tokens", minTokens,
		"--reporters", "json",
		"--output", outDir,
		"--absolute",
		"--no-gitignore",
		"--no-colors", "--no-tips", "--silent",
	}
	full = append(full, wrapcli.ToAbs(cwd, files)...)
	cmd := exec.Command(jscpdBin, full...)
	var jstderr bytes.Buffer
	cmd.Stderr = &jstderr
	if runErr := cmd.Run(); runErr != nil {
		return 0, fmt.Errorf("run jscpd (%s): %w\n%s",
			jscpdBin, runErr, strings.TrimSpace(jstderr.String()))
	}

	rep, err := readReport(filepath.Join(outDir, reportName))
	if err != nil {
		return 0, err
	}
	fl := newFileLines()
	surviving, err := emit(rep, fl, cwd, machine, stdout)
	if err != nil {
		return 0, err
	}
	banned, err := emitIgnoreBans(fl, wrapcli.ToAbs(cwd, files), cwd, machine, stdout)
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

func parseArgs(args []string) (machine bool, jscpdBin string, files []string, err error) {
	for i := 0; i < len(args); i++ {
		a := args[i]
		switch {
		case a == "--machine":
			machine = true
		case a == "--jscpd":
			if i+1 >= len(args) {
				return false, "", nil, errors.New("--jscpd requires a path argument")
			}
			i++
			jscpdBin = args[i]
		case strings.HasPrefix(a, "--jscpd="):
			jscpdBin = strings.TrimPrefix(a, "--jscpd=")
		default:
			files = append(files, a)
		}
	}
	return machine, jscpdBin, files, nil
}

type endpoint struct {
	Name     string `json:"name"`
	Start    int    `json:"start"`
	End      int    `json:"end"`
	StartLoc struct {
		Line int `json:"line"`
	} `json:"startLoc"`
	EndLoc struct {
		Line int `json:"line"`
	} `json:"endLoc"`
}

// lineNo is the endpoint's reported line: startLoc.line when jscpd supplied it,
// else the flat start field (the two agree in 5.0.12 output).
func (e endpoint) lineNo() int {
	if e.StartLoc.Line > 0 {
		return e.StartLoc.Line
	}
	return e.Start
}

func (e endpoint) endLineNo() int {
	if e.EndLoc.Line > 0 {
		return e.EndLoc.Line
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

func readReport(path string) (*jscpdReport, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read jscpd report %s: %w", path, err)
	}
	var rep jscpdReport
	if err := json.Unmarshal(data, &rep); err != nil {
		return nil, fmt.Errorf("parse jscpd report %s: %w", path, err)
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

// emit writes findings and returns the surviving endpoint count. Java clones
// confined to file headers are dropped first (never output); then dup-ok above
// one endpoint drops only that endpoint, above both drops the clone. Machine
// mode writes one NDJSON object per surviving endpoint; human mode writes one
// pair line per clone that keeps at least one endpoint.
func emit(rep *jscpdReport, fl *fileLines, cwd string, machine bool, w io.Writer) (int, error) {
	enc := json.NewEncoder(w)
	surviving := 0
	for _, c := range rep.Duplicates {
		headerOnly, err := javaHeaderClone(c, fl)
		if err != nil {
			return 0, err
		}
		if headerOnly {
			continue
		}
		aSup, err := suppressed(fl, c.FirstFile)
		if err != nil {
			return 0, err
		}
		bSup, err := suppressed(fl, c.SecondFile)
		if err != nil {
			return 0, err
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
		if aSup {
			sup = append(sup, aRel)
		}
		if bSup {
			sup = append(sup, bRel)
		}
		if len(sup) > 0 {
			line += " [dup-ok: " + strings.Join(sup, ", ") + "]"
		}
		if _, err := fmt.Fprintln(w, line); err != nil {
			return 0, err
		}
		surviving += n
	}
	return surviving, nil
}

func relTo(cwd, name string) string {
	rel, err := filepath.Rel(cwd, name)
	if err != nil || strings.HasPrefix(rel, "..") {
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

// suppressed reports whether the standalone comment block directly above the
// endpoint's start line carries a `dup-ok: <non-empty reason>` marker.
// Semantics mirror go/internal/markers.Above: the block's last line must be
// startLine-1, and the marker may sit on any line of that contiguous block.
// Only whole-line // and # comments count - a trailing comment after code is
// not a standalone block.
func suppressed(fl *fileLines, e endpoint) (bool, error) {
	startLine := e.lineNo()
	if startLine < 2 {
		return false, nil
	}
	lines, err := fl.get(e.Name)
	if err != nil {
		return false, fmt.Errorf("dup-ok check: %w", err)
	}
	for ln := startLine - 1; ln >= 1; ln-- {
		if ln > len(lines) {
			return false, nil
		}
		body, ok := commentBody(strings.TrimSpace(lines[ln-1]))
		if !ok {
			return false, nil
		}
		if reason, ok := dupOkReason(body); ok && reason != "" {
			return true, nil
		}
	}
	return false, nil
}

// commentBody strips a leading line-comment marker (// or #) and returns the
// trimmed remainder; ok is false when the line is not a whole-line comment.
func commentBody(text string) (string, bool) {
	switch {
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

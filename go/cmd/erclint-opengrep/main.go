// Command erclint-opengrep runs opengrep with the bundled erclint
// ruleset. The rules are embedded into the binary at build time so
// the binary is self-contained; opengrep itself must be available
// on PATH at run time.
package main

import (
	"bytes"
	"context"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/nikitatsym/tackbox/go/internal/wrapcli"
	"github.com/nikitatsym/tackbox/go/report"
)

//go:embed all:rules
var rulesFS embed.FS

// version is injected at build time via -ldflags "-X main.version=...".
var version = "dev"

func main() {
	wrapcli.Main("erclint-opengrep", version, run)
}

func run(args []string, stdout, stderr io.Writer) (int, error) {
	origCwd, err := os.Getwd()
	if err != nil {
		return 0, fmt.Errorf("get cwd: %w", err)
	}
	if err := rejectSemgrepignore(origCwd); err != nil {
		return 0, err
	}

	args, err = expandPathsFrom(args)
	if err != nil {
		return 0, err
	}
	machine, scanArgs := splitMachine(args)

	rulesDir, err := os.MkdirTemp("", "erclint-rules-*")
	if err != nil {
		return 0, fmt.Errorf("create rules dir: %w", err)
	}
	defer os.RemoveAll(rulesDir)
	if err := extractRules(rulesDir); err != nil {
		return 0, fmt.Errorf("extract rules: %w", err)
	}

	// Empty .semgrepignore in the scan cwd suppresses opengrep's builtin
	// default-ignore patterns (tests/, node_modules/, ...); those must not
	// silently drop files the caller explicitly passed in.
	scanCwd, err := os.MkdirTemp("", "erclint-scan-*")
	if err != nil {
		return 0, fmt.Errorf("create scan cwd: %w", err)
	}
	defer os.RemoveAll(scanCwd)
	if err := os.WriteFile(filepath.Join(scanCwd, ".semgrepignore"), nil, 0o644); err != nil {
		return 0, fmt.Errorf("write empty .semgrepignore: %w", err)
	}

	// opengrep has no targets-from-file flag, so a huge file set is split under an
	// argv byte budget and scanned in batches; the merged exit code is the max (2
	// opengrep-error > 1 findings > 0 clean). The bundled rules are per-file, so a
	// file lives in exactly one batch and the batched result equals one scan -
	// machine dedup keys on (file,line,rule), which never spans batches.
	targets := wrapcli.ToAbs(origCwd, scanArgs)
	maxCode := 0
	for _, batch := range chunkByArgvBytes(targets, maxScanArgvBytes) {
		code, err := scanBatch(batch, rulesDir, scanCwd, machine, origCwd, stdout, stderr)
		if err != nil {
			return 0, err
		}
		if code > maxCode {
			maxCode = code
		}
	}
	return maxCode, nil
}

// maxScanArgvBytes caps the target bytes per opengrep invocation, well under
// ARG_MAX once the small base args and the environment are added.
const maxScanArgvBytes = 128 * 1024

// scanBatch runs opengrep over one batch of targets and writes its (translated,
// path-rewritten) output to stdout/stderr, returning opengrep's exit code.
func scanBatch(targets []string, rulesDir, scanCwd string, machine bool, origCwd string, stdout, stderr io.Writer) (int, error) {
	full := []string{"scan", "--config", rulesDir, "--error"}
	if machine {
		full = append(full, "--json")
	}
	full = append(full, targets...)
	cmd := exec.Command("opengrep", full...)
	cmd.Dir = scanCwd
	var outBuf, errBuf bytes.Buffer
	cmd.Stdout = &outBuf
	cmd.Stderr = &errBuf

	runErr := cmd.Run()

	// Opengrep sees absolute paths; rewrite them back so consumers see the
	// same shape they would if opengrep ran directly in origCwd. Machine mode
	// translates opengrep's stable JSON into the internal one-finding-per-line
	// {file, line, rule} contract instead of the decorative text.
	if machine {
		if err := emitMachine(stdout, outBuf.Bytes(), origCwd); err != nil {
			return 0, fmt.Errorf("translate opengrep json: %w", err)
		}
	} else if _, err := io.WriteString(stdout, rewritePaths(outBuf.String(), origCwd)); err != nil {
		return 0, fmt.Errorf("write stdout: %w", err)
	}
	if _, err := io.WriteString(stderr, rewritePaths(errBuf.String(), origCwd)); err != nil {
		return 0, fmt.Errorf("write stderr: %w", err)
	}

	// A non-ExitError means opengrep could not be invoked - a real failure.
	// An ExitError (or nil) means it ran; its exit code is data (0 clean, 1
	// findings, 2 opengrep error) that we propagate as our own via ProcessState.
	if runErr != nil && !errors.As(runErr, new(*exec.ExitError)) {
		return 0, fmt.Errorf("invoke opengrep (must be on PATH): %w", runErr)
	}
	return cmd.ProcessState.ExitCode(), nil
}

// chunkByArgvBytes splits targets into batches each within budget bytes of argv.
// Empty input yields one empty batch so opengrep still runs once (scanning the
// empty scan cwd), preserving the no-target behavior.
func chunkByArgvBytes(targets []string, budget int) [][]string {
	if len(targets) == 0 {
		return [][]string{nil}
	}
	var chunks [][]string
	var cur []string
	curBytes := 0
	for _, t := range targets {
		n := len(t) + 1
		if len(cur) > 0 && curBytes+n > budget {
			chunks = append(chunks, cur)
			cur = nil
			curBytes = 0
		}
		cur = append(cur, t)
		curBytes += n
	}
	return append(chunks, cur)
}

func rejectSemgrepignore(dir string) error {
	fi, err := os.Stat(filepath.Join(dir, ".semgrepignore"))
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("stat .semgrepignore in %s: %w", dir, err)
	}
	if fi.IsDir() {
		return nil
	}
	return fmt.Errorf(
		".semgrepignore in %s is not supported: tackbox does not allow "+
			"configurable engine excludes; use suppression markers "+
			"(// no-report: ..., // parse-skip: ..., // nil-return: ...) instead",
		dir,
	)
}

func rewritePaths(s, cwd string) string {
	if s == "" || cwd == "" {
		return s
	}
	return strings.ReplaceAll(s, cwd+string(os.PathSeparator), "")
}

func extractRules(dst string) error {
	return fs.WalkDir(rulesFS, "rules", func(p string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		rel, err := filepath.Rel("rules", p)
		if err != nil {
			return err
		}
		target := filepath.Join(dst, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		data, err := rulesFS.ReadFile(p)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, 0o644)
	})
}

// expandPathsFrom replaces a `--paths-from <file>` pair with the scan targets
// the list-file holds, injected where positional files went (ARG_MAX safety).
func expandPathsFrom(args []string) ([]string, error) {
	var out []string
	for i := 0; i < len(args); i++ {
		if args[i] == "--paths-from" {
			if i+1 >= len(args) {
				return nil, errors.New("--paths-from requires a file argument")
			}
			i++
			paths, err := wrapcli.ReadPathList(args[i])
			if err != nil {
				return nil, err
			}
			out = append(out, paths...)
			continue
		}
		out = append(out, args[i])
	}
	return out, nil
}

// splitMachine strips the internal --machine flag (opengrep JSON translated to
// the {file, line, rule} contract) out of the scan args.
func splitMachine(args []string) (bool, []string) {
	machine := false
	var out []string
	for _, a := range args {
		if a == "--machine" {
			machine = true
			continue
		}
		out = append(out, a)
	}
	return machine, out
}

// emitMachine translates opengrep's --json output into the internal contract:
// one {file, line, rule} object per line. Paths are made repo-relative and
// check_id is reduced to its final segment (the rule id, the temp rules dir
// prefix dropped). A whole-output parse failure surfaces as an error, never a
// silent drop. Findings are deduped by (rule, path, line): a rule can bind the
// same offending line via several metavariables and opengrep emits one result
// per binding, but the caller contract is one finding per located line.
func emitMachine(w io.Writer, jsonOut []byte, cwd string) error {
	if len(bytes.TrimSpace(jsonOut)) == 0 {
		return nil
	}
	enc := json.NewEncoder(w)
	var parsed struct {
		Results []struct {
			CheckID string `json:"check_id"`
			Path    string `json:"path"`
			Start   struct {
				Line int `json:"line"`
			} `json:"start"`
			Extra struct {
				Message string `json:"message"`
			} `json:"extra"`
		} `json:"results"`
	}
	if err := json.Unmarshal(jsonOut, &parsed); err != nil {
		report.Error(context.Background(),
			"opengrep --json output unparseable", err, nil, "erclint-opengrep.machine")
		// Never drop a finding: a location-unknown record makes the caller
		// over-report rather than silently see zero findings.
		return enc.Encode(wrapcli.Finding{Rule: "opengrep-json-unparseable"})
	}
	// Dedup key excludes Message: duplicate bindings on one line may
	// interpolate different metavariables into it; the first message wins.
	type key struct {
		file string
		line int
		rule string
	}
	seen := map[key]bool{}
	for _, r := range parsed.Results {
		rule := r.CheckID
		if i := strings.LastIndex(rule, "."); i >= 0 {
			rule = rule[i+1:]
		}
		f := wrapcli.Finding{
			File:    rewritePaths(r.Path, cwd),
			Line:    r.Start.Line,
			Rule:    rule,
			Message: r.Extra.Message,
		}
		if seen[key{f.File, f.Line, f.Rule}] {
			continue
		}
		seen[key{f.File, f.Line, f.Rule}] = true
		if err := enc.Encode(f); err != nil {
			return err
		}
	}
	return nil
}

// Package wrapcli holds the shared entrypoint scaffolding for the engine
// wrapper commands (erclint-opengrep, tackbox-jscpd): --version handling,
// optional Sentry report init, and the run-to-exit contract.
package wrapcli

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/nikitatsym/tackbox/go/report"
)

// Run is a wrapper's core: parse args, do the work, return an exit code. A
// non-nil error is an infra failure (spawn/parse) surfaced as exit 2, never a
// silent clean.
type Run func(args []string, stdout, stderr io.Writer) (int, error)

// Main is the shared wrapper entrypoint: name is the command/release name,
// version its build-stamped version, run its core. Handles --version, optional
// Sentry init from the env DSN, and turns run's (code, err) into the process
// exit - err becomes a Sentry capture, a stderr line, and exit 2.
func Main(name, version string, run Run) {
	for _, a := range os.Args[1:] {
		if a == "--version" || a == "-version" {
			fmt.Printf("%s %s\n", name, version)
			return
		}
	}
	if dsn := report.DSNFromEnv(); dsn != "" {
		// no-report: report itself failed, capture would be a no-op
		if err := report.Init(report.Options{
			DSN:           dsn,
			Release:       name,
			SilentMissing: true,
		}); err != nil {
			fmt.Fprintln(os.Stderr, name+": report init:", err)
		}
		defer report.Flush()
	}
	code, err := run(os.Args[1:], os.Stdout, os.Stderr)
	if err != nil {
		report.Error(context.Background(), "wrapper failed", err, map[string]string{"area": "wrapcli.run", "bin": name}, "wrapcli.run")
		fmt.Fprintln(os.Stderr, name+":", err)
		os.Exit(2)
	}
	// no-report: normal exit
	os.Exit(code)
}

// ReadPathList reads a newline-separated list-file (UTF-8), returning its
// non-empty lines. Engines take the file/package list through such a file, not
// as thousands of positional args, so the spawn stays under ARG_MAX (E2BIG) on
// large repos.
func ReadPathList(path string) ([]string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read path list %s: %w", path, err)
	}
	var out []string
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSuffix(line, "\r")
		if line != "" {
			out = append(out, line)
		}
	}
	return out, nil
}

// ToAbs resolves each non-absolute arg against cwd, leaving absolutes alone.
func ToAbs(cwd string, args []string) []string {
	out := make([]string, len(args))
	for i, a := range args {
		if filepath.IsAbs(a) {
			out[i] = a
			continue
		}
		out[i] = filepath.Join(cwd, a)
	}
	return out
}

// Finding is the internal machine-mode contract: one JSON object per located
// finding.
type Finding struct {
	File    string `json:"file"`
	Line    int    `json:"line"`
	Rule    string `json:"rule"`
	Message string `json:"message,omitempty"`
}

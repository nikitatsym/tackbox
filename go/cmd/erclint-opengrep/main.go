// Command erclint-opengrep runs opengrep with the bundled erclint
// ruleset. The rules are embedded into the binary at build time so
// the binary is self-contained; opengrep itself must be available
// on PATH at run time.
package main

import (
	"bytes"
	"context"
	"embed"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/nikitatsym/tackbox/go/report"
)

//go:embed all:rules
var rulesFS embed.FS

// version is injected at build time via -ldflags "-X main.version=...".
var version = "dev"

func main() {
	for _, arg := range os.Args[1:] {
		if arg == "--version" || arg == "-version" {
			fmt.Printf("erclint-opengrep %s\n", version)
			return
		}
	}
	if dsn := report.DSNFromEnv(); dsn != "" {
		// no-sentry: report itself failed, capture would be a no-op
		if err := report.Init(report.Options{
			DSN:           dsn,
			Release:       "erclint-opengrep",
			SilentMissing: true,
		}); err != nil {
			fmt.Fprintln(os.Stderr, "erclint-opengrep: report init:", err)
		}
		defer report.Flush()
	}
	code, err := run(os.Args[1:], os.Stdout, os.Stderr)
	if err != nil {
		report.SentryErr(context.Background(),
			"opengrep wrapper failed",
			err, nil, "erclint-opengrep.run")
		fmt.Fprintln(os.Stderr, "erclint-opengrep:", err)
		os.Exit(2)
	}
	// no-sentry: normal exit
	os.Exit(code)
}

func run(args []string, stdout, stderr io.Writer) (int, error) {
	origCwd, err := os.Getwd()
	if err != nil {
		return 0, fmt.Errorf("get cwd: %w", err)
	}
	if err := rejectSemgrepignore(origCwd); err != nil {
		return 0, err
	}

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

	full := append([]string{"scan", "--config", rulesDir, "--error"}, toAbs(origCwd, args)...)
	cmd := exec.Command("opengrep", full...)
	cmd.Dir = scanCwd
	var outBuf, errBuf bytes.Buffer
	cmd.Stdout = &outBuf
	cmd.Stderr = &errBuf

	runErr := cmd.Run()

	// Opengrep sees absolute paths; rewrite them back so consumers see the
	// same shape they would if opengrep ran directly in origCwd.
	if _, err := io.WriteString(stdout, rewritePaths(outBuf.String(), origCwd)); err != nil {
		return 0, fmt.Errorf("write stdout: %w", err)
	}
	if _, err := io.WriteString(stderr, rewritePaths(errBuf.String(), origCwd)); err != nil {
		return 0, fmt.Errorf("write stderr: %w", err)
	}

	if runErr != nil {
		var exitErr *exec.ExitError
		if errors.As(runErr, &exitErr) {
			return exitErr.ExitCode(), nil
		}
		return 0, fmt.Errorf("invoke opengrep (must be on PATH): %w", runErr)
	}
	return 0, nil
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
			"(// no-sentry: ..., // parse-skip: ..., // nil-return: ...) instead",
		dir,
	)
}

func toAbs(cwd string, args []string) []string {
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

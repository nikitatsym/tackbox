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

	scanArgs, pyNames, javaNames := splitReporters(args)

	rulesDir, err := os.MkdirTemp("", "erclint-rules-*")
	if err != nil {
		return 0, fmt.Errorf("create rules dir: %w", err)
	}
	defer os.RemoveAll(rulesDir)
	if err := extractRules(rulesDir, pyNames, javaNames); err != nil {
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

	full := append([]string{"scan", "--config", rulesDir, "--error"}, toAbs(origCwd, scanArgs)...)
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

func extractRules(dst string, pyNames, javaNames []string) error {
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
		switch d.Name() {
		case "exceptions-python.yaml":
			data = injectReporters(data, pyNames, pythonReporterBlock)
		case "exceptions-java.yaml":
			data = injectReporters(data, javaNames, javaReporterBlock)
		}
		return os.WriteFile(target, data, 0o644)
	})
}

const reportersFlag = "--reporters="

// splitReporters strips `--reporters=file#func,...` out of the scan args and
// buckets the declared function names by source language. opengrep is the
// syntactic tier: the file is only used to pick python vs java, the symbol is
// not resolved.
func splitReporters(args []string) (scan, py, java []string) {
	for _, a := range args {
		if !strings.HasPrefix(a, reportersFlag) {
			scan = append(scan, a)
			continue
		}
		for _, d := range strings.Split(a[len(reportersFlag):], ",") {
			hash := strings.LastIndex(d, "#")
			if hash <= 0 {
				continue
			}
			switch filepath.Ext(d[:hash]) {
			case ".py":
				py = append(py, d[hash+1:])
			case ".java":
				java = append(java, d[hash+1:])
			}
		}
	}
	return scan, py, java
}

// injectReporters splices a pattern-not per declared name into the swallowed
// rule, just before the no-sentry escape. A declared reporter that the caught
// error flows into ($E in its args) is then not a swallow.
func injectReporters(data []byte, names []string, block func(string) string) []byte {
	if len(names) == 0 {
		return data
	}
	const anchor = "      # no-sentry escape;"
	idx := strings.Index(string(data), anchor)
	if idx < 0 {
		return data
	}
	var b strings.Builder
	for _, n := range names {
		b.WriteString(block(n))
	}
	return []byte(string(data[:idx]) + b.String() + string(data[idx:]))
}

func pythonReporterBlock(name string) string {
	return "      - pattern-not: |\n" +
		"          try:\n" +
		"              ...\n" +
		"          except $T as $E:\n" +
		"              ...\n" +
		"              " + name + "(..., $E, ...)\n"
}

func javaReporterBlock(name string) string {
	return "      - pattern-not: |\n" +
		"          try { ... } catch ($T $E) {\n" +
		"            ...\n" +
		"            " + name + "(..., $E, ...);\n" +
		"            ...\n" +
		"          }\n"
}

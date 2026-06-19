// Command erclint-opengrep runs opengrep with the bundled erclint
// ruleset. The rules are embedded into the binary at build time so
// the binary is self-contained; opengrep itself must be available
// on PATH at run time.
package main

import (
	"context"
	"embed"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/nikitatsym/tackbox/go/report"
)

//go:embed all:rules
var rulesFS embed.FS

func main() {
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
	code, err := run(os.Args[1:])
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

func run(args []string) (int, error) {
	tmp, err := os.MkdirTemp("", "erclint-rules-*")
	if err != nil {
		return 0, fmt.Errorf("create temp dir: %w", err)
	}
	defer os.RemoveAll(tmp)

	if err := extractRules(tmp); err != nil {
		return 0, fmt.Errorf("extract rules: %w", err)
	}

	full := append([]string{"scan", "--config", tmp, "--error"}, args...)
	cmd := exec.Command("opengrep", full...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			return exitErr.ExitCode(), nil
		}
		return 0, fmt.Errorf("invoke opengrep (must be on PATH): %w", err)
	}
	return 0, nil
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

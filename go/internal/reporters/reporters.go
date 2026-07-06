// Package reporters resolves erclint's `--reporters` value into the
// declared-reporter set the capture analyzers consume. Each declaration is
// `<abs-file>#<function>`; the function is validated to exist (dead symbol =
// hard error), independent of the lint scope. A file that does not resolve to
// a package in the current module context is skipped - its own module's run
// validates it.
package reporters

import (
	"fmt"
	"go/types"
	"path/filepath"
	"strings"

	"golang.org/x/tools/go/packages"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

func Resolve(spec string) ([]astutil.DeclaredReporter, error) {
	var out []astutil.DeclaredReporter
	for _, entry := range strings.Split(spec, ",") {
		if entry == "" {
			continue
		}
		hash := strings.LastIndex(entry, "#")
		if hash <= 0 {
			return nil, fmt.Errorf(".tackbox-reporters: malformed declaration %q", entry)
		}
		file, fn := entry[:hash], entry[hash+1:]
		pkg, err := loadFile(file)
		if err != nil {
			return nil, err
		}
		if pkg == nil {
			continue
		}
		if !hasFunc(pkg, fn) {
			return nil, fmt.Errorf(".tackbox-reporters: no top-level function %s in %s", fn, file)
		}
		out = append(out, astutil.DeclaredReporter{PkgPath: pkg.PkgPath, Name: fn})
	}
	return out, nil
}

func loadFile(file string) (*packages.Package, error) {
	// Source-mode load: export-data mode (NeedTypes alone) only surfaces
	// unexported top-level funcs the compiler happened to inline into an
	// exported caller, so an unreferenced or package-main sink is invisible.
	cfg := &packages.Config{
		Mode: packages.NeedName | packages.NeedFiles | packages.NeedSyntax | packages.NeedTypes | packages.NeedTypesInfo,
		Dir:  filepath.Dir(file),
	}
	pkgs, err := packages.Load(cfg, "file="+file)
	if err != nil {
		return nil, fmt.Errorf(".tackbox-reporters: cannot load %s: %w", file, err)
	}
	if len(pkgs) == 0 || pkgs[0].Types == nil || pkgs[0].Types.Scope() == nil {
		return nil, nil
	}
	return pkgs[0], nil
}

func hasFunc(pkg *packages.Package, name string) bool {
	obj := pkg.Types.Scope().Lookup(name)
	if obj == nil {
		return false
	}
	_, ok := obj.(*types.Func)
	return ok
}

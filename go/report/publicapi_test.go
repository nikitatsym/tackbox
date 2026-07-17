package report

import (
	"go/types"
	"reflect"
	"sort"
	"testing"

	"golang.org/x/tools/go/packages"
)

type apiField struct {
	name string
	typ  string
}

// TestPublicAPIIsReportingOnly pins the exported surface of go/report to the
// reporting-only set: two types and twelve functions, each with an exact
// signature. Signatures and field types use the golang.org/x/tools loader with
// a package-relative qualifier, so cross-package names render by import path.
func TestPublicAPIIsReportingOnly(t *testing.T) {
	const pkgPath = "github.com/nikitatsym/tackbox/go/report"

	wantFuncs := map[string]string{
		"Init":        "func Init(opts Options) error",
		"Ready":       "func Ready() bool",
		"Verify":      "func Verify(timeout time.Duration) error",
		"Flush":       "func Flush(timeout ...time.Duration)",
		"DSNFromEnv":  "func DSNFromEnv() string",
		"SetNotifier": "func SetNotifier(fn func(Notice))",
		"Error":       "func Error(ctx context.Context, msg string, err error, tags map[string]string, dedupKey string)",
		"Warn":        "func Warn(ctx context.Context, msg string, err error, tags map[string]string, dedupKey string)",
		"Quiet":       "func Quiet(ctx context.Context, msg string, err error, tags map[string]string, dedupKey string)",
		"Notify":      "func Notify(ctx context.Context, msg string, err error, tags map[string]string, dedupKey string)",
		"Panic":       "func Panic(name string, recovered any)",
		"Crumb":       "func Crumb(category string, message string, data map[string]any)",
	}

	wantFields := map[string][]apiField{
		"Options": {
			{"DSN", "string"},
			{"Release", "string"},
			{"Environment", "string"},
			{"FlushTimeout", "time.Duration"},
			{"RateWindow", "time.Duration"},
			{"Debug", "bool"},
			{"Verify", "bool"},
			{"VerifyTimeout", "time.Duration"},
			{"SilentMissing", "bool"},
			{"Logger", "*log/slog.Logger"},
		},
		"Notice": {
			{"Msg", "string"},
			{"Level", "string"},
			{"Tags", "map[string]string"},
			{"DedupKey", "string"},
			{"Cause", "error"},
		},
	}

	cfg := &packages.Config{Mode: packages.NeedName | packages.NeedTypes}
	pkgs, err := packages.Load(cfg, pkgPath)
	if err != nil {
		t.Fatalf("load %s: %v", pkgPath, err)
	}
	if len(pkgs) != 1 {
		t.Fatalf("want exactly 1 package, got %d", len(pkgs))
	}
	p := pkgs[0]
	if len(p.Errors) > 0 {
		t.Fatalf("package load errors: %v", p.Errors)
	}

	qf := types.RelativeTo(p.Types)
	scope := p.Types.Scope()

	got := map[string]types.Object{}
	for _, name := range scope.Names() {
		if obj := scope.Lookup(name); obj.Exported() {
			got[name] = obj
		}
	}

	want := map[string]bool{}
	for name := range wantFuncs {
		want[name] = true
	}
	for name := range wantFields {
		want[name] = true
	}

	var missing, extra []string
	for name := range want {
		if _, ok := got[name]; !ok {
			missing = append(missing, name)
		}
	}
	for name := range got {
		if !want[name] {
			extra = append(extra, name)
		}
	}
	sort.Strings(missing)
	sort.Strings(extra)
	if len(missing) > 0 {
		t.Errorf("missing exports (pinned but absent from package): %v", missing)
	}
	if len(extra) > 0 {
		t.Errorf("extra exports (present in package, not pinned): %v", extra)
	}

	for name, wantSig := range wantFuncs {
		obj, ok := got[name]
		if !ok {
			continue
		}
		fn, ok := obj.(*types.Func)
		if !ok {
			t.Errorf("%s: exported as %T, want *types.Func", name, obj)
			continue
		}
		if gotSig := types.ObjectString(fn, qf); gotSig != wantSig {
			t.Errorf("%s signature:\n got  %s\n want %s", name, gotSig, wantSig)
		}
	}

	for name, wantFs := range wantFields {
		obj, ok := got[name]
		if !ok {
			continue
		}
		tn, ok := obj.(*types.TypeName)
		if !ok {
			t.Errorf("%s: exported as %T, want *types.TypeName", name, obj)
			continue
		}
		st, ok := tn.Type().Underlying().(*types.Struct)
		if !ok {
			t.Errorf("%s: underlying %T, want struct", name, tn.Type().Underlying())
			continue
		}
		var gotFs []apiField
		for i := 0; i < st.NumFields(); i++ {
			f := st.Field(i)
			gotFs = append(gotFs, apiField{f.Name(), types.TypeString(f.Type(), qf)})
		}
		if !reflect.DeepEqual(gotFs, wantFs) {
			t.Errorf("%s fields:\n got  %v\n want %v", name, gotFs, wantFs)
		}
	}
}

"""Engine registry, dispatch, and parallel subprocess runner.

Two registries share the dispatch shape: DEV_ENGINES (source checkout)
and HERMETIC_ENGINES (installed wheel with tackbox_engines).

Signal-killed subprocess exit code is normalized to `128 + sig`
(Python maps signal-kill to `-sig`).
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .source_set import (
    files_to_go_packages,
    group_go_packages_by_module,
    module_relative,
)

# (repo_root, tackbox_root, args, reporters) -> argv.
# reporters = (repo-relative-file, function) pairs from .tackbox-reporters.
ArgvBuilder = Callable[
    [Path, Path, list[str], "tuple[tuple[str, str], ...]"], list[str]
]

_TACKBOX_PKG_ROOT = Path(__file__).parent


def is_hermetic() -> bool:
    if not (_TACKBOX_PKG_ROOT / "engines.json").is_file():
        return False
    try:
        import tackbox_engines  # noqa: F401
    except ImportError:
        return False
    return True


def hermetic_engines_root() -> Path:
    import tackbox_engines

    return tackbox_engines.root()


def hermetic_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    er = hermetic_engines_root()
    bin_dir = er / "bin"
    node_modules = er / "vendor" / "node_modules"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["NODE_PATH"] = str(node_modules)
    return env


def exe_name(name: str) -> str:
    return f"{name}.exe" if sys.platform.startswith("win") else name


@dataclass(frozen=True)
class EngineSpec:
    id: str
    extensions: frozenset[str]
    build_argv: ArgvBuilder
    # If True, `.go` files are collapsed to package dirs before argv assembly.
    package_mode: bool = False
    # Per-path predicate applied after extension match; drop when False.
    # Used to encode language conventions like Go's `testdata/` exclusion.
    path_filter: Callable[[str], bool] = lambda _p: True


@dataclass(frozen=True)
class EngineResult:
    engine_id: str
    exit_code: int
    stdout: str
    stderr: str


def normalize_exit_code(rc: int) -> int:
    if rc < 0:
        return 128 + (-rc)
    return rc


def dispatch(
    files: list[str], engines: list[EngineSpec]
) -> list[tuple[EngineSpec, list[str]]]:
    """Pair each engine with the subset of files it lints.

    Order of the returned list follows `engines`. Engines with no matching
    files are dropped; engines in package_mode receive package dirs, not
    files.
    """
    plan: list[tuple[EngineSpec, list[str]]] = []
    for engine in engines:
        subset = [
            f for f in files
            if _has_ext(f, engine.extensions) and engine.path_filter(f)
        ]
        if not subset:
            continue
        args = files_to_go_packages(subset) if engine.package_mode else subset
        if not args:
            continue
        plan.append((engine, args))
    return plan


def run_engines(
    plan: list[tuple[EngineSpec, list[str]]],
    repo_root: Path,
    tackbox_root: Path,
    reporters: tuple[tuple[str, str], ...] = (),
) -> list[EngineResult]:
    """Run each dispatched engine as a subprocess in parallel."""
    if not plan:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(plan)) as pool:
        futures = {
            pool.submit(
                _run_one, engine, args, repo_root, tackbox_root, reporters
            ): engine.id
            for engine, args in plan
        }
        results = [fut.result() for fut in concurrent.futures.as_completed(futures)]
    return sorted(results, key=lambda r: r.engine_id)


def _run_one(
    engine: EngineSpec,
    args: list[str],
    repo_root: Path,
    tackbox_root: Path,
    reporters: tuple[tuple[str, str], ...] = (),
) -> EngineResult:
    if engine.package_mode:
        return _run_per_module(engine, args, repo_root, tackbox_root, reporters)
    argv = engine.build_argv(repo_root, tackbox_root, args, reporters)
    env = hermetic_env() if is_hermetic() else None
    completed = subprocess.run(
        argv,
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return EngineResult(
        engine_id=engine.id,
        exit_code=normalize_exit_code(completed.returncode),
        stdout=completed.stdout.decode("utf-8", errors="replace"),
        stderr=completed.stderr.decode("utf-8", errors="replace"),
    )


def _run_per_module(
    engine: EngineSpec,
    args: list[str],
    repo_root: Path,
    tackbox_root: Path,
    reporters: tuple[tuple[str, str], ...] = (),
) -> EngineResult:
    """One subprocess per Go module, cwd at the module root.

    `go list`-style patterns resolve against the module containing cwd,
    so packages from different modules cannot share one invocation.
    """
    groups, orphans = group_go_packages_by_module(
        args, lambda d: (repo_root / d / "go.mod").is_file()
    )
    env = hermetic_env() if is_hermetic() else None
    max_code = 0
    outs: list[str] = []
    errs: list[str] = []
    for module in sorted(groups):
        rel = [module_relative(module, p) for p in groups[module]]
        argv = engine.build_argv(repo_root, tackbox_root, rel, reporters)
        completed = subprocess.run(
            argv,
            cwd=repo_root / module,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        max_code = max(max_code, normalize_exit_code(completed.returncode))
        outs.append(completed.stdout.decode("utf-8", errors="replace"))
        errs.append(completed.stderr.decode("utf-8", errors="replace"))
    for pkg in orphans:
        errs.append(f"no enclosing go.mod, skipped: {pkg}\n")
    return EngineResult(
        engine_id=engine.id,
        exit_code=max_code,
        stdout="".join(outs),
        stderr="".join(errs),
    )


def parse_erclint_findings(raw: str) -> list[dict]:
    """Flatten erclint's -json output into a list of findings.

    Empty input, blank input, or JSON `{}` yield an empty list. Multiple
    concatenated JSON objects (one per module run) are merged - import
    paths are globally unique. Analyzer load errors (`{"error": "..."}`
    in place of a finding list) bubble up as ValueError - dev mode never
    silently drops them.
    """
    text = raw.strip()
    if not text:
        return []
    findings: list[dict] = []
    for doc in iter_json_objects(text):
        for pkg, analyzers in doc.items():
            for analyzer, payload in analyzers.items():
                if isinstance(payload, dict) and "error" in payload:
                    raise ValueError(
                        f"erclint analyzer {analyzer!r} failed for {pkg!r}: "
                        f"{payload['error']}"
                    )
                for item in payload:
                    findings.append({"pkg": pkg, "analyzer": analyzer, **item})
    return findings


def iter_json_objects(text: str):
    """Iterate over concatenated top-level JSON objects in `text`."""
    decoder = json.JSONDecoder()
    idx = 0
    n = len(text)
    while idx < n:
        while idx < n and text[idx].isspace():
            idx += 1
        if idx >= n:
            break
        obj, end = decoder.raw_decode(text, idx)
        yield obj
        idx = end


def _has_ext(path: str, exts: frozenset[str]) -> bool:
    dot = path.rfind(".")
    if dot < 0:
        return False
    return path[dot:] in exts


def _reporters_flag(
    reporters: tuple[tuple[str, str], ...],
    exts: frozenset[str],
    transform: Callable[[str], str],
) -> list[str]:
    """Format `--reporters=<path>#<func>,...` for one engine's language.

    Only declarations whose file matches `exts` are passed; each engine
    self-filters so the same declaration set reaches every builder. erclint
    gets absolute paths (its `file=` load is cwd-independent); the syntactic
    engines get the paths as written.
    """
    picked = [
        f"{transform(f)}#{fn}" for f, fn in reporters if _has_ext(f, exts)
    ]
    return [f"--reporters={','.join(picked)}"] if picked else []


# --- Dev-mode engine specs ------------------------------------------------

_JS_EXTS = frozenset(
    [".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".svelte"]
)
_MD_EXTS = frozenset([".md"])
_GO_EXTS = frozenset([".go"])
# Extensions matched by any bundled opengrep rule (svelte omitted - no parser).
_OPENGREP_EXTS = frozenset(
    [".go", ".py", ".java", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"]
)
# Reporter declarations the opengrep wrapper substitutes: only the syntactic
# swallowed-exception tier (Go -> erclint, JS/TS -> eslint own their decls).
_OPENGREP_DECL_EXTS = frozenset([".py", ".java"])


def _built_go_binary(tackbox_root: Path, name: str) -> Path:
    build_dir = tackbox_root / ".tackbox-dev" / "bin"
    build_dir.mkdir(parents=True, exist_ok=True)
    bin_path = build_dir / name
    subprocess.run(
        ["go", "build", "-o", str(bin_path), f"./go/cmd/{name}"],
        cwd=tackbox_root,
        check=True,
    )
    return bin_path


def resolve_dev_versions(tackbox_root: Path) -> dict[str, str]:
    """Resolve local versions for the banner (`?` when unavailable).

    Only used by the CLI banner; kept here so registry and version
    resolution share the same binary-location logic.
    """
    return {
        "erclint": _erclint_dev_version(tackbox_root),
        "opengrep": _version_from_binary("opengrep", ("--version",)),
        "node": _version_from_binary("node", ("--version",), strip_v=True),
        "eslint": _version_from_npm_manifest(tackbox_root, "eslint"),
        "markdownlint": _version_from_npm_manifest(tackbox_root, "markdownlint"),
    }


def _erclint_dev_version(tackbox_root: Path) -> str:
    # The dev binary is built on demand; without a Go toolchain the build
    # itself fails, and the banner must degrade to "?" rather than crash.
    try:
        bin_ = _built_go_binary(tackbox_root, "erclint")
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "?"
    return _version_from_binary(bin_, ("--version",), prefix="erclint ")


def _version_from_binary(
    binary, args: tuple[str, ...], prefix: str = "", strip_v: bool = False
) -> str:
    try:
        result = subprocess.run(
            [str(binary), *args], capture_output=True, check=True, text=True
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "?"
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if not line:
        return "?"
    if prefix and line.startswith(prefix):
        line = line[len(prefix):]
    if strip_v and line.startswith("v"):
        line = line[1:]
    return line or "?"


def _version_from_npm_manifest(tackbox_root: Path, pkg: str) -> str:
    manifest = tackbox_root / "node_modules" / pkg / "package.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return "?"
    return data.get("version") or "?"


def _erclint_argv(
    repo_root: Path, tackbox_root: Path, pkgs: list[str], reporters=()
) -> list[str]:
    bin_ = _built_go_binary(tackbox_root, "erclint")
    flag = _reporters_flag(reporters, _GO_EXTS, lambda f: str(repo_root / f))
    return [str(bin_), "-json", *flag, *(f"./{p}" for p in pkgs)]


def _erclint_opengrep_argv(
    _repo_root: Path, tackbox_root: Path, files: list[str], reporters=()
) -> list[str]:
    bin_ = _built_go_binary(tackbox_root, "erclint-opengrep")
    flag = _reporters_flag(reporters, _OPENGREP_DECL_EXTS, lambda f: f)
    return [str(bin_), *flag, *files]


def _tackbox_eslint_argv(
    _repo_root: Path, tackbox_root: Path, files: list[str], reporters=()
) -> list[str]:
    flag = _reporters_flag(reporters, _JS_EXTS, lambda f: f)
    return ["node", str(tackbox_root / "bin" / "tackbox-eslint.js"), *flag, *files]


def _tackbox_mdlint_argv(
    _repo_root: Path, tackbox_root: Path, files: list[str], _reporters=()
) -> list[str]:
    return ["node", str(tackbox_root / "bin" / "tackbox-mdlint.js"), *files]


def _drop_go_testdata(path: str) -> bool:
    """Go convention: `testdata/` at any level is not part of any package."""
    if path.endswith(".go") and "testdata" in path.split("/")[:-1]:
        return False
    return True


DEV_ENGINES: list[EngineSpec] = [
    EngineSpec(
        id="erclint",
        extensions=_GO_EXTS,
        build_argv=_erclint_argv,
        package_mode=True,
        path_filter=_drop_go_testdata,
    ),
    EngineSpec(
        id="erclint-opengrep",
        extensions=_OPENGREP_EXTS,
        build_argv=_erclint_opengrep_argv,
        path_filter=_drop_go_testdata,
    ),
    EngineSpec(
        id="tackbox-eslint",
        extensions=_JS_EXTS,
        build_argv=_tackbox_eslint_argv,
    ),
    EngineSpec(
        id="tackbox-mdlint",
        extensions=_MD_EXTS,
        build_argv=_tackbox_mdlint_argv,
    ),
]


# --- Hermetic-mode engine specs -------------------------------------------


def _hermetic_erclint_bin(name: str) -> Path:
    return _TACKBOX_PKG_ROOT / "bin" / exe_name(name)


def _hermetic_node_bin() -> Path:
    return hermetic_engines_root() / "bin" / exe_name("node")


def _hermetic_rule_script(name: str) -> Path:
    return _TACKBOX_PKG_ROOT / "rules" / "bin" / name


def _erclint_argv_hermetic(
    repo_root: Path, _tackbox_root: Path, pkgs: list[str], reporters=()
) -> list[str]:
    flag = _reporters_flag(reporters, _GO_EXTS, lambda f: str(repo_root / f))
    return [
        str(_hermetic_erclint_bin("erclint")),
        "-json",
        *flag,
        *(f"./{p}" for p in pkgs),
    ]


def _erclint_opengrep_argv_hermetic(
    _repo_root: Path, _tackbox_root: Path, files: list[str], reporters=()
) -> list[str]:
    flag = _reporters_flag(reporters, _OPENGREP_DECL_EXTS, lambda f: f)
    return [str(_hermetic_erclint_bin("erclint-opengrep")), *flag, *files]


def _tackbox_eslint_argv_hermetic(
    _repo_root: Path, _tackbox_root: Path, files: list[str], reporters=()
) -> list[str]:
    flag = _reporters_flag(reporters, _JS_EXTS, lambda f: f)
    return [
        str(_hermetic_node_bin()),
        str(_hermetic_rule_script("tackbox-eslint.js")),
        *flag,
        *files,
    ]


def _tackbox_mdlint_argv_hermetic(
    _repo_root: Path, _tackbox_root: Path, files: list[str], _reporters=()
) -> list[str]:
    return [
        str(_hermetic_node_bin()),
        str(_hermetic_rule_script("tackbox-mdlint.js")),
        *files,
    ]


HERMETIC_ENGINES: list[EngineSpec] = [
    EngineSpec(
        id="erclint",
        extensions=_GO_EXTS,
        build_argv=_erclint_argv_hermetic,
        package_mode=True,
        path_filter=_drop_go_testdata,
    ),
    EngineSpec(
        id="erclint-opengrep",
        extensions=_OPENGREP_EXTS,
        build_argv=_erclint_opengrep_argv_hermetic,
        path_filter=_drop_go_testdata,
    ),
    EngineSpec(
        id="tackbox-eslint",
        extensions=_JS_EXTS,
        build_argv=_tackbox_eslint_argv_hermetic,
    ),
    EngineSpec(
        id="tackbox-mdlint",
        extensions=_MD_EXTS,
        build_argv=_tackbox_mdlint_argv_hermetic,
    ),
]


def active_engines() -> list[EngineSpec]:
    return HERMETIC_ENGINES if is_hermetic() else DEV_ENGINES


def load_engines_json() -> dict:
    return json.loads((_TACKBOX_PKG_ROOT / "engines.json").read_text())


def resolve_hermetic_versions() -> dict[str, str]:
    data = load_engines_json()
    versions = {e["id"]: e.get("version", "?") for e in data.get("engines", [])}
    return {
        "erclint": versions.get("erclint", "?"),
        "opengrep": versions.get("opengrep", "?"),
        "node": versions.get("node", "?"),
        "eslint": versions.get("eslint", "?"),
        "markdownlint": versions.get("markdownlint", "?"),
    }


def engines_hash_hermetic() -> str:
    return load_engines_json().get("payload_sha256", "?")

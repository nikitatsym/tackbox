"""Engine registry, dispatch, and parallel subprocess runner.

Dev mode: engines are locally-built binaries (Go) and Node scripts under
the tackbox source tree. Step 5 will replace this registry with wheel-bundled
binaries; the public shape of dispatch/run stays the same.

Exit-code semantics:
- Each engine's subprocess return code is normalized: a negative value
  (Python maps signal-kill to `-sig`) becomes `128 + sig`.
- The aggregate CLI exit is `max` of the normalized codes; zero engines
  dispatched is exit 0.
"""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .source_set import files_to_go_packages

ArgvBuilder = Callable[[Path, Path, list[str]], list[str]]


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
) -> list[EngineResult]:
    """Run each dispatched engine as a subprocess in parallel."""
    if not plan:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(plan)) as pool:
        futures = {
            pool.submit(_run_one, engine, args, repo_root, tackbox_root): engine.id
            for engine, args in plan
        }
        results = [fut.result() for fut in concurrent.futures.as_completed(futures)]
    return sorted(results, key=lambda r: r.engine_id)


def _run_one(
    engine: EngineSpec,
    args: list[str],
    repo_root: Path,
    tackbox_root: Path,
) -> EngineResult:
    argv = engine.build_argv(repo_root, tackbox_root, args)
    completed = subprocess.run(
        argv,
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return EngineResult(
        engine_id=engine.id,
        exit_code=normalize_exit_code(completed.returncode),
        stdout=completed.stdout.decode("utf-8", errors="replace"),
        stderr=completed.stderr.decode("utf-8", errors="replace"),
    )


def parse_erclint_findings(raw: str) -> list[dict]:
    """Flatten erclint's -json output into a list of findings.

    Empty input, blank input, or JSON `{}` yield an empty list. Analyzer
    load errors (`{"error": "..."}` in place of a finding list) bubble up
    as ValueError - dev mode never silently drops them.
    """
    text = raw.strip()
    if not text:
        return []
    doc = json.loads(text)
    findings: list[dict] = []
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


def _has_ext(path: str, exts: frozenset[str]) -> bool:
    dot = path.rfind(".")
    if dot < 0:
        return False
    return path[dot:] in exts


# --- Dev-mode engine specs ------------------------------------------------

_JS_EXTS = frozenset(
    [".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".svelte"]
)
_MD_EXTS = frozenset([".md"])
_GO_EXTS = frozenset([".go"])
# Extensions matched by any bundled opengrep rule (svelte omitted - no parser).
_OPENGREP_EXTS = frozenset(
    [".go", ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"]
)


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


def _erclint_argv(_repo_root: Path, tackbox_root: Path, pkgs: list[str]) -> list[str]:
    bin_ = _built_go_binary(tackbox_root, "erclint")
    return [str(bin_), "-json", *(f"./{p}" for p in pkgs)]


def _erclint_opengrep_argv(
    _repo_root: Path, tackbox_root: Path, files: list[str]
) -> list[str]:
    bin_ = _built_go_binary(tackbox_root, "erclint-opengrep")
    return [str(bin_), *files]


def _tackbox_eslint_argv(
    _repo_root: Path, tackbox_root: Path, files: list[str]
) -> list[str]:
    return ["node", str(tackbox_root / "bin" / "tackbox-eslint.js"), *files]


def _tackbox_mdlint_argv(
    _repo_root: Path, tackbox_root: Path, files: list[str]
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

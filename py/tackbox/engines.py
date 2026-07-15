"""Engine registry, dispatch, and parallel subprocess runner.

Two registries share the dispatch shape: DEV_ENGINES (source checkout)
and HERMETIC_ENGINES (installed thin wheel; engine binaries come from the
machine-versioned store, see ensure_engines).

Signal-killed subprocess exit code is normalized to `128 + sig`
(Python maps signal-kill to `-sig`).
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

from .hashing import sha256_file, sha256_tree
from .pyrules.codes import CODE_TO_ID
from .source_set import (
    files_to_go_packages,
    group_go_packages_by_module,
    module_relative,
)

# (repo_root, tackbox_root, args, reporters) -> argv.
# reporters = (repo-relative-file, function, kind) triples from .tackbox-reporters.
ArgvBuilder = Callable[
    [Path, Path, list[str], "tuple[tuple[str, str, str], ...]"], list[str]
]

_TACKBOX_PKG_ROOT = Path(__file__).parent

# Runtime (system, machine) -> platform key. Mirrors doctor and build_wheels;
# the store's platform assert and doctor name a host/wheel drift identically.
_PLATFORM_KEYS = {
    ("linux", "x86_64"): "linux-x86_64",
    ("linux", "amd64"): "linux-x86_64",
    ("linux", "aarch64"): "linux-aarch64",
    ("linux", "arm64"): "linux-aarch64",
    ("darwin", "arm64"): "macos-aarch64",
    ("darwin", "aarch64"): "macos-aarch64",
    ("windows", "x86_64"): "windows-x86_64",
    ("windows", "amd64"): "windows-x86_64",
}

ENGINES_DIR_ENV = "TACKBOX_ENGINES_DIR"


class EnginesStoreError(RuntimeError):
    """The engine store could not be resolved, fetched, or verified."""


def detect_platform_key() -> str | None:
    system = sys.platform
    if system.startswith("linux"):
        system = "linux"
    elif system.startswith("win") or system == "cygwin":
        system = "windows"
    return _PLATFORM_KEYS.get((system, platform.machine().lower()))


def is_hermetic() -> bool:
    """True when running from an installed thin wheel, False in a source checkout.

    engines.json is baked into the wheel and absent from a clean checkout, but a
    local `build_wheels.py` run leaves a gitignored copy behind - so presence
    alone is not enough. A checkout is the tree whose repo root (two levels up)
    still carries the engine sources and dev.py; a site-packages install never
    does.
    """
    if not (_TACKBOX_PKG_ROOT / "engines.json").is_file():
        return False
    repo = _TACKBOX_PKG_ROOT.parent.parent
    if (repo / "dev.py").is_file() and (repo / "go").is_dir():
        return False
    return True


def engines_store_base() -> Path:
    """`$XDG_DATA_HOME/tackbox/engines` (default `~/.local/share/...`)."""
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "tackbox" / "engines"


def hermetic_engines_root() -> Path:
    """The engine payload root (holds bin/ vendor/ third_party/).

    TACKBOX_ENGINES_DIR overrides it with a directly-supplied payload (tests,
    CI, pre-publish smoke); otherwise it is the versioned store directory. Pure
    path resolver - ensure_engines() is what fetches on absence.
    """
    override = os.environ.get(ENGINES_DIR_ENV)
    if override:
        return Path(override)
    return engines_store_base() / str(load_engines_json()["engines_version"])


# Fetcher seam: (engines.json, workdir) -> local path of the downloaded fat
# wheel. Injected in tests; _download_fat_wheel is the PyPI default.
Fetcher = Callable[[dict, Path], Path]


def ensure_engines(fetcher: "Fetcher | None" = None) -> Path:
    """Guarantee the engine payload exists locally; return its root.

    Fetches the fat wheel from PyPI once per engines version, verifies it
    against the engines.json pins, and installs it atomically into the store.
    A present store (or a TACKBOX_ENGINES_DIR override) short-circuits with no
    network. Every failure is loud - there is no silent fallback.
    """
    if os.environ.get(ENGINES_DIR_ENV):
        return Path(os.environ[ENGINES_DIR_ENV])
    data = load_engines_json()
    _assert_runtime_platform(data)
    root = engines_store_base() / str(data["engines_version"])
    if root.is_dir():
        return root
    _fetch_and_install(data, root, fetcher or _download_fat_wheel)
    _gc_store_siblings(root)
    return root


def _assert_runtime_platform(data: dict) -> None:
    """Refuse a fat wheel pinned for a different platform than this host.

    Catches a wrong-wheel install (manual, or an x86 interpreter under Rosetta
    on arm64) before any download - both platforms named in the error.
    """
    pinned = (data.get("fat_wheel") or {}).get("platform")
    detected = detect_platform_key()
    if pinned and detected and pinned != detected:
        raise EnginesStoreError(
            f"platform drift: engines.json pins the fat wheel for {pinned!r} "
            f"but this host resolves to {detected!r} (wrong wheel installed, "
            f"or an emulated interpreter?)"
        )


def _fetch_and_install(data: dict, root: Path, fetcher: "Fetcher") -> None:
    base = root.parent
    base.mkdir(parents=True, exist_ok=True)
    # Dot-prefixed so a concurrent fetch's temp is skipped by _gc_store_siblings.
    work = Path(tempfile.mkdtemp(prefix=".ensure-", dir=base))
    try:
        wheel = fetcher(data, work)
        # Integrity is pinned on the payload TREE, not the wheel file: rebuilds
        # of a published engines version are not zip-reproducible and PyPI
        # keeps the first upload, so container bytes legitimately differ.
        staged = work / "store"
        _unpack_tackbox_engines(wheel, staged)
        tree = sha256_tree(staged)
        if tree != data["store_sha256"]:
            raise EnginesStoreError(
                f"engines payload tree sha256 mismatch after unpack: pinned "
                f"{data['store_sha256']}, got {tree}"
            )
        try:
            os.rename(staged, root)
        except OSError:
            # no-report: lost the init race - the winner's store is committed
            # and verified identical, so use it and drop ours
            if not root.is_dir():
                raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _unpack_tackbox_engines(wheel: Path, dest: Path) -> None:
    """Extract the wheel's `tackbox_engines/` subtree into dest, restoring the
    executable bit the wheel recorded (node / opengrep must stay runnable)."""
    prefix = "tackbox_engines/"
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as zf:
        members = [
            n for n in zf.namelist() if n.startswith(prefix) and not n.endswith("/")
        ]
        if not members:
            raise EnginesStoreError(f"fat wheel {wheel.name} has no {prefix} payload")
        for name in members:
            info = zf.getinfo(name)
            target = dest / name[len(prefix):]
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                os.chmod(target, mode)


def engines_payload_tree_sha256(wheel: Path) -> str:
    """sha256_tree of a fat wheel's tackbox_engines payload, computed exactly as
    the store verifies it at install time. The build stamps this into
    engines.json (store_sha256); ensure/doctor recompute it against the store,
    so both sides go through the same unpack + digest and can never drift."""
    with tempfile.TemporaryDirectory() as td:
        staged = Path(td) / "store"
        _unpack_tackbox_engines(wheel, staged)
        return sha256_tree(staged)


def _gc_store_siblings(current: Path) -> None:
    """Drop every version sibling except `current` (gc_stale_engines policy for
    the store). Dot-prefixed entries are in-flight fetches - never touched."""
    base = current.parent
    for entry in base.iterdir():
        if (
            entry.is_dir()
            and entry.name != current.name
            and not entry.name.startswith(".")
        ):
            shutil.rmtree(entry, ignore_errors=True)


_PYPI_ENGINES_JSON = "https://pypi.org/pypi/tackbox-engines/{version}/json"


def _download_fat_wheel(data: dict, workdir: Path) -> Path:
    """Resolve the fat wheel for this version via the PyPI JSON API and download
    it (stdlib only). Loud on any transport or lookup failure."""
    version = str(data["engines_version"])
    want_name = data["fat_wheel"]["wheel"]
    index_url = _PYPI_ENGINES_JSON.format(version=version)
    try:
        with urlopen(index_url, timeout=60) as r:
            meta = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise EnginesStoreError(f"cannot fetch engines index {index_url}: {e}") from e
    entry = next(
        (u for u in meta.get("urls", []) if u.get("filename") == want_name), None
    )
    if entry is None or not entry.get("url"):
        raise EnginesStoreError(f"engines wheel {want_name} not found in {index_url}")
    dl_url = entry["url"]
    dest = workdir / want_name
    try:
        with urlopen(dl_url, timeout=300) as r, dest.open("wb") as out:
            shutil.copyfileobj(r, out)
    except OSError as e:
        raise EnginesStoreError(f"cannot download engines wheel {dl_url}: {e}") from e
    # Transport integrity against the index's own digest (truncation, corrupt
    # proxy); substitution is caught later by the store_sha256 tree pin.
    claimed = (entry.get("digests") or {}).get("sha256")
    if claimed:
        got = sha256_file(dest)
        if got != claimed:
            raise EnginesStoreError(
                f"downloaded {want_name} does not match the index digest: "
                f"index {claimed}, got {got}"
            )
    return dest


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
    # Accepts the internal --machine flag (one {file, line, rule} JSON per
    # finding). erclint is False: its -json output is parsed directly.
    machine_flag: bool = False
    # Included in the per-file content cache. jscpd is False: duplication is
    # cross-file, so a per-file key is false by construction - it always runs
    # and never writes clean markers.
    cacheable: bool = True


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


@dataclass(frozen=True)
class EngineRun:
    """Everything one engine invocation needs, bundled so it flows unchanged
    from run_engines through the dispatch helpers (frozen: read-only en route)."""

    engine: EngineSpec
    args: list[str]
    repo_root: Path
    tackbox_root: Path
    reporters: tuple[tuple[str, str, str], ...] = ()
    machine: bool = False


def run_engines(
    plan: list[tuple[EngineSpec, list[str]]],
    repo_root: Path,
    tackbox_root: Path,
    reporters: tuple[tuple[str, str, str], ...] = (),
    machine: bool = False,
) -> list[EngineResult]:
    """Run each dispatched engine as a subprocess in parallel.

    machine=True asks every machine-capable engine for the internal
    one-JSON-object-per-finding output instead of its human format.
    """
    if not plan:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(plan)) as pool:
        futures = {
            pool.submit(
                _run_one,
                EngineRun(engine, args, repo_root, tackbox_root, reporters, machine),
            ): engine.id
            for engine, args in plan
        }
        results = [fut.result() for fut in concurrent.futures.as_completed(futures)]
    return sorted(results, key=lambda r: r.engine_id)


def _machine_argv(engine: EngineSpec, argv: list[str], machine: bool) -> list[str]:
    return [*argv, "--machine"] if machine and engine.machine_flag else argv


def _run_one(run: EngineRun) -> EngineResult:
    if run.engine.package_mode:
        return _run_per_module(run)
    argv = _machine_argv(
        run.engine,
        run.engine.build_argv(run.repo_root, run.tackbox_root, run.args, run.reporters),
        run.machine,
    )
    env = hermetic_env() if is_hermetic() else None
    completed = subprocess.run(
        argv,
        cwd=run.repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return EngineResult(
        engine_id=run.engine.id,
        exit_code=normalize_exit_code(completed.returncode),
        stdout=completed.stdout.decode("utf-8", errors="replace"),
        stderr=completed.stderr.decode("utf-8", errors="replace"),
    )


def _run_per_module(run: EngineRun) -> EngineResult:
    """One subprocess per Go module, cwd at the module root.

    `go list`-style patterns resolve against the module containing cwd,
    so packages from different modules cannot share one invocation.
    """
    groups, orphans = group_go_packages_by_module(
        run.args, lambda d: (run.repo_root / d / "go.mod").is_file()
    )
    env = hermetic_env() if is_hermetic() else None
    max_code = 0
    outs: list[str] = []
    errs: list[str] = []
    for module in sorted(groups):
        rel = [module_relative(module, p) for p in groups[module]]
        argv = _machine_argv(
            run.engine,
            run.engine.build_argv(run.repo_root, run.tackbox_root, rel, run.reporters),
            run.machine,
        )
        completed = subprocess.run(
            argv,
            cwd=run.repo_root / module,
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
        engine_id=run.engine.id,
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


COMPILE_SKIP = "analysis skipped due to errors in package"


def erclint_compile_broken_pkgs(stdout: str) -> list[str]:
    """Base packages whose erclint run was skipped because they do not compile.
    `pkg`, `pkg [pkg.test]`, and `pkg.test` variants collapse to one entry."""
    bases: list[str] = []
    seen: set[str] = set()
    for doc in iter_json_objects(stdout):
        for pkg, analyzers in doc.items():
            if not any(
                isinstance(p, dict) and p.get("error") == COMPILE_SKIP
                for p in analyzers.values()
            ):
                continue
            base = pkg.split(" [", 1)[0]
            if base.endswith(".test"):
                base = base[: -len(".test")]
            if base not in seen:
                seen.add(base)
                bases.append(base)
    return bases


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


@dataclass(frozen=True)
class Finding:
    """A located finding for the hook's diff-scope. file/line are None when the
    engine could not attribute a location; the caller over-reports such a
    finding rather than dropping it."""

    rule: str
    file: str | None
    line: int | None
    message: str | None = None


def parse_machine_findings(stdout: str) -> list[Finding]:
    """Parse the internal one-JSON-object-per-line machine output. Bins emit
    valid JSON per finding; a missing file/line becomes None (location
    unknown)."""
    out: list[Finding] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        out.append(
            Finding(
                rule=obj.get("rule") or "",
                file=obj.get("file"),
                line=obj.get("line"),
                message=obj.get("message") or None,
            )
        )
    return out


def _split_posn(posn: str) -> tuple[str | None, int | None]:
    # erclint posn is `path:line:col`; rsplit from the right keeps a Windows
    # drive colon intact. An unexpected shape yields a location-unknown finding.
    parts = posn.rsplit(":", 2)
    if len(parts) == 3 and parts[1].isdigit():
        return parts[0], int(parts[1])
    return None, None


def erclint_located_findings(stdout: str, repo_root: Path) -> list[Finding]:
    out: list[Finding] = []
    for f in parse_erclint_findings(stdout):
        path, line = _split_posn(f.get("posn", ""))
        rel = os.path.relpath(path, repo_root) if path is not None else None
        out.append(
            Finding(
                rule=f.get("analyzer", ""), file=rel, line=line,
                message=f.get("message") or None,
            )
        )
    return out


def javalint_located_findings(stdout: str, _repo_root: Path) -> list[Finding]:
    """javalint's JSON outer key is the repo-relative file it was handed (python
    passes repo-relative paths, cwd at the repo root), so - unlike erclint's
    absolute posn - the file is taken verbatim, no relpath. Only the line comes
    from posn."""
    out: list[Finding] = []
    for f in parse_erclint_findings(stdout):
        _, line = _split_posn(f.get("posn", ""))
        out.append(
            Finding(
                rule=f.get("analyzer", ""), file=f.get("pkg"), line=line,
                message=f.get("message") or None,
            )
        )
    return out


# flake8's `path:row:col: CODE msg`. file is non-greedy so a windows drive colon
# stays with the path; only the TBX code is tokenized (the message carries colons).
_FLAKE8_LINE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):\d+: (?P<code>TBX\d+) (?P<msg>.*)$")


def pyrules_located_findings(stdout: str, _repo_root: Path) -> list[Finding]:
    """Parse flake8's `path:row:col: TBXNNN <id>: <msg>` lines. The rule id comes
    from the TBX code via CODE_TO_ID; the leading `<id>: ` echo is stripped from
    the message so downstream `rule: message` lines do not repeat the id."""
    out: list[Finding] = []
    for line in stdout.splitlines():
        m = _FLAKE8_LINE.match(line)
        if m is None:
            continue
        rule = CODE_TO_ID.get(m["code"], m["code"])
        msg = m["msg"]
        if msg.startswith(rule + ": "):
            msg = msg[len(rule) + 2:]
        out.append(
            Finding(
                rule=rule,
                file=m["file"],
                line=int(m["line"]),
                message=msg or None,
            )
        )
    return out


def located_findings(engine_id: str, stdout: str, repo_root: Path) -> list[Finding]:
    """Located findings from one engine's output: erclint from its -json posn,
    javalint from its erclint-shaped JSON (repo-relative keys), pyrules from
    flake8's text, every other engine from its machine NDJSON."""
    if engine_id == "erclint":
        return erclint_located_findings(stdout, repo_root)
    if engine_id == "javalint":
        return javalint_located_findings(stdout, repo_root)
    if engine_id == "pyrules":
        return pyrules_located_findings(stdout, repo_root)
    return parse_machine_findings(stdout)


def _has_ext(path: str, exts: frozenset[str]) -> bool:
    dot = path.rfind(".")
    if dot < 0:
        return False
    return path[dot:] in exts


def _reporters_flag(
    reporters: tuple[tuple[str, str, str], ...],
    exts: frozenset[str],
    transform: Callable[[str], str],
    kind: str = "capture",
    flag: str = "--reporters",
) -> list[str]:
    """Format `<flag>=<path>#<func>,...` for one engine's language and kind.

    Each engine self-filters the shared declaration set. Capture sinks ride
    `--reporters`; usage sinks ride `--usage-sinks` (erclint-only today).
    erclint gets absolute paths (its `file=` load is cwd-independent); the
    syntactic engines get the paths as written.
    """
    picked = [
        f"{transform(f)}#{fn}"
        for f, fn, k in reporters
        if k == kind and _has_ext(f, exts)
    ]
    return [f"{flag}={','.join(picked)}"] if picked else []


# --- Dev-mode engine specs ------------------------------------------------

_JS_EXTS = frozenset(
    [".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".svelte"]
)
_MD_EXTS = frozenset([".md"])
_GO_EXTS = frozenset([".go"])
_PY_EXTS = frozenset([".py"])
_JAVA_EXTS = frozenset([".java"])
# Extensions matched by any bundled opengrep rule. Go-only now: the sole
# surviving rule is exceptions-go (go-exit-in-recover). Every other language's
# rules handle reporter recognition in their own engine - erclint (Go ERC006,
# by origin), eslint (JS/TS, by origin), pyrules (Python, by declared name) -
# so opengrep no longer scans py/js/ts.
_OPENGREP_EXTS = _GO_EXTS
# jscpd tokenizes go/py/java and the JS family (svelte included); markdown is
# excluded as noise (prose duplication is not a defect).
_JSCPD_EXTS = _GO_EXTS | _PY_EXTS | _JAVA_EXTS | _JS_EXTS


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
    try:
        bin_ = _built_go_binary(tackbox_root, "erclint")
    except (FileNotFoundError, subprocess.CalledProcessError):
        # no-report: no go toolchain - the dev binary builds on demand, so the
        # version banner degrades to "?" rather than crash
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
        # no-report: binary missing or non-zero exit - the version banner degrades to "?"
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
        # no-report: manifest missing or unparseable - the version banner degrades to "?"
        return "?"
    return data.get("version") or "?"


def _erclint_argv(
    repo_root: Path, tackbox_root: Path, pkgs: list[str], reporters=()
) -> list[str]:
    bin_ = _built_go_binary(tackbox_root, "erclint")
    flag = _reporters_flag(reporters, _GO_EXTS, lambda f: str(repo_root / f))
    usage = _reporters_flag(
        reporters,
        _GO_EXTS,
        lambda f: str(repo_root / f),
        kind="usage",
        flag="--usage-sinks",
    )
    return [str(bin_), "-json", *flag, *usage, *(f"./{p}" for p in pkgs)]


def _erclint_opengrep_argv(
    _repo_root: Path, tackbox_root: Path, files: list[str], reporters=()
) -> list[str]:
    bin_ = _built_go_binary(tackbox_root, "erclint-opengrep")
    return [str(bin_), *files]


def _dev_jscpd_bin(tackbox_root: Path) -> Path:
    """Host jscpd binary for dev mode, fetched + extracted per engines/manifest.json
    into .tackbox-dev/bin, cached by the tarball sha256.

    Dev checkouts have no engine store (opengrep rides the system PATH; jscpd has
    no such install), so the wrapper is pointed at this copy. The archive sha is
    verified on download - a mismatch fails loudly, never yields a stale binary.
    """
    key = detect_platform_key()
    if key is None:
        raise EnginesStoreError(
            f"no jscpd manifest platform for host {sys.platform}/{platform.machine()}"
        )
    manifest = json.loads((tackbox_root / "engines" / "manifest.json").read_text())
    entry = manifest["platforms"][key]["jscpd"]
    archive_sha = entry["archive_sha256"]
    bin_dir = tackbox_root / ".tackbox-dev" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if sys.platform.startswith("win") else ""
    bin_path = bin_dir / f"jscpd-{archive_sha[:16]}{suffix}"
    if bin_path.is_file():
        return bin_path
    with tempfile.TemporaryDirectory() as td:
        tgz = Path(td) / "jscpd.tgz"
        with urlopen(entry["source_url"], timeout=300) as r, tgz.open("wb") as out:
            shutil.copyfileobj(r, out)
        got = sha256_file(tgz)
        if got != archive_sha:
            raise EnginesStoreError(
                f"jscpd tarball sha256 mismatch for {entry['source_url']}: "
                f"expected {archive_sha}, got {got}"
            )
        with tarfile.open(tgz, "r:gz") as tf:
            src = tf.extractfile(entry["archive_member"])
            if src is None:
                raise EnginesStoreError(
                    f"member {entry['archive_member']} not in {entry['source_url']}"
                )
            tmp = bin_dir / f".jscpd-{os.getpid()}.tmp"
            with tmp.open("wb") as out:
                shutil.copyfileobj(src, out)
    os.chmod(tmp, 0o755)
    os.replace(tmp, bin_path)
    return bin_path


def _tackbox_jscpd_argv(
    _repo_root: Path, tackbox_root: Path, files: list[str], reporters=()
) -> list[str]:
    bin_ = _built_go_binary(tackbox_root, "tackbox-jscpd")
    jscpd = _dev_jscpd_bin(tackbox_root)
    return [str(bin_), "--jscpd", str(jscpd), *files]


def _javalint_src_hash(tackbox_root: Path) -> str:
    """Digest of the java sources + pom that determine the shaded jar. Keys the
    dev-jar cache slot and (via cache._DEV_PAYLOAD) invalidates consumer content
    caches when a java rule changes."""
    h = hashlib.sha256()
    h.update(b"javalint-src-v1\n")
    src = tackbox_root / "java" / "src" / "main"
    if src.is_dir():
        h.update(sha256_tree(src).encode())
    h.update(b"\t")
    pom = tackbox_root / "java" / "pom.xml"
    if pom.is_file():
        h.update(sha256_file(pom).encode())
    return h.hexdigest()


def _built_javalint_jar(tackbox_root: Path) -> Path:
    """Build the shaded javalint.jar with maven, cached by java-source hash in
    .tackbox-dev. Unlike the go dev binaries (rebuilt each run, fast via go's
    own cache), `mvn package` has no cheap no-op, so a source change alone
    triggers a rebuild; the jar is the same one build_wheels.py packs into thin."""
    build_dir = tackbox_root / ".tackbox-dev"
    build_dir.mkdir(parents=True, exist_ok=True)
    jar = build_dir / f"javalint-{_javalint_src_hash(tackbox_root)}.jar"
    if jar.is_file():
        return jar
    mvn = shutil.which("mvn") or "mvn"
    subprocess.run(
        [mvn, "-q", "-B", "-f", str(tackbox_root / "java" / "pom.xml"),
         "-DskipTests", "package"],
        cwd=tackbox_root,
        check=True,
    )
    built = tackbox_root / "java" / "target" / "javalint.jar"
    if not built.is_file():
        raise FileNotFoundError(f"expected shaded jar at {built} after mvn package")
    # Publish into the hash-named slot atomically so a concurrent lint never
    # reads a half-copied jar.
    tmp = build_dir / f".javalint-{os.getpid()}.jar.tmp"
    shutil.copyfile(built, tmp)
    os.replace(tmp, jar)
    return jar


def _javalint_argv(
    _repo_root: Path, tackbox_root: Path, files: list[str], reporters=()
) -> list[str]:
    jar = _built_javalint_jar(tackbox_root)
    flag = _reporters_flag(reporters, _JAVA_EXTS, lambda f: f)
    return ["java", "-jar", str(jar), *flag, *files]


def _tackbox_eslint_argv(
    _repo_root: Path, tackbox_root: Path, files: list[str], reporters=()
) -> list[str]:
    flag = _reporters_flag(reporters, _JS_EXTS, lambda f: f)
    return ["node", str(tackbox_root / "bin" / "tackbox-eslint.js"), *flag, *files]


def _tackbox_mdlint_argv(
    _repo_root: Path, tackbox_root: Path, files: list[str], _reporters=()
) -> list[str]:
    return ["node", str(tackbox_root / "bin" / "tackbox-mdlint.js"), *files]


def _pyrules_argv(
    _repo_root: Path, _tackbox_root: Path, files: list[str], reporters=()
) -> list[str]:
    """flake8 in closed form. It runs under the same interpreter as tackbox
    (dev: the uv env; hermetic: the uvx venv), so it discovers the TBX plugin
    entry point in both. Shared by the dev and hermetic registries."""
    flag = _reporters_flag(reporters, _PY_EXTS, lambda f: f)
    return [
        sys.executable, "-m", "flake8",
        "--isolated", "--disable-noqa", "--select=TBX",
        *flag, *files,
    ]


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
        machine_flag=True,
    ),
    EngineSpec(
        id="tackbox-jscpd",
        extensions=_JSCPD_EXTS,
        build_argv=_tackbox_jscpd_argv,
        path_filter=_drop_go_testdata,
        machine_flag=True,
        cacheable=False,
    ),
    EngineSpec(
        id="javalint",
        extensions=_JAVA_EXTS,
        build_argv=_javalint_argv,
    ),
    EngineSpec(
        id="tackbox-eslint",
        extensions=_JS_EXTS,
        build_argv=_tackbox_eslint_argv,
        machine_flag=True,
    ),
    EngineSpec(
        id="tackbox-mdlint",
        extensions=_MD_EXTS,
        build_argv=_tackbox_mdlint_argv,
        machine_flag=True,
    ),
    EngineSpec(
        id="pyrules",
        extensions=_PY_EXTS,
        build_argv=_pyrules_argv,
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
    usage = _reporters_flag(
        reporters,
        _GO_EXTS,
        lambda f: str(repo_root / f),
        kind="usage",
        flag="--usage-sinks",
    )
    return [
        str(_hermetic_erclint_bin("erclint")),
        "-json",
        *flag,
        *usage,
        *(f"./{p}" for p in pkgs),
    ]


def _erclint_opengrep_argv_hermetic(
    _repo_root: Path, _tackbox_root: Path, files: list[str], reporters=()
) -> list[str]:
    return [str(_hermetic_erclint_bin("erclint-opengrep")), *files]


def _tackbox_jscpd_argv_hermetic(
    _repo_root: Path, _tackbox_root: Path, files: list[str], reporters=()
) -> list[str]:
    """Wrapper from the thin wheel; jscpd from the versioned engine store. Unlike
    node/opengrep the wrapper takes the store path explicitly (--jscpd) rather
    than resolving off PATH, so dev and hermetic share one wrapper contract."""
    jscpd = hermetic_engines_root() / "bin" / exe_name("jscpd")
    return [str(_hermetic_erclint_bin("tackbox-jscpd")), "--jscpd", str(jscpd), *files]


def _javalint_argv_hermetic(
    _repo_root: Path, _tackbox_root: Path, files: list[str], reporters=()
) -> list[str]:
    """`java` is the system toolchain (like go), not a bundled binary; the jar
    rides in the thin wheel. hermetic_env keeps the system PATH so java resolves;
    doctor's java-toolchain check gates its presence + version."""
    jar = _TACKBOX_PKG_ROOT / "bin" / "javalint.jar"
    flag = _reporters_flag(reporters, _JAVA_EXTS, lambda f: f)
    return ["java", "-jar", str(jar), *flag, *files]


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
        machine_flag=True,
    ),
    EngineSpec(
        id="tackbox-jscpd",
        extensions=_JSCPD_EXTS,
        build_argv=_tackbox_jscpd_argv_hermetic,
        path_filter=_drop_go_testdata,
        machine_flag=True,
        cacheable=False,
    ),
    EngineSpec(
        id="javalint",
        extensions=_JAVA_EXTS,
        build_argv=_javalint_argv_hermetic,
    ),
    EngineSpec(
        id="tackbox-eslint",
        extensions=_JS_EXTS,
        build_argv=_tackbox_eslint_argv_hermetic,
        machine_flag=True,
    ),
    EngineSpec(
        id="tackbox-mdlint",
        extensions=_MD_EXTS,
        build_argv=_tackbox_mdlint_argv_hermetic,
        machine_flag=True,
    ),
    EngineSpec(
        id="pyrules",
        extensions=_PY_EXTS,
        build_argv=_pyrules_argv,
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

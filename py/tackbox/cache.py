"""(unit, engine) cache: layout, digests, marker ops, GC.

Layout: `<TACKBOX_CACHE_HOME>/<CACHE_VERSION>/<engines-hash>/<unit-digest>.<engine-id>`.
Marker is an empty file. `TACKBOX_CACHE_HOME` defaults to `~/.cache/tackbox`;
tests point it at a tmp dir.

Semantics per plan:
- Marker written only on success. Failures are not cached.
- Cache hit re-touches the marker: mtime is the LRU signal.
- Corrupt / unreadable marker -> treated as miss (rerun), never fatal.
- `mark_clean` is best-effort; cache is an optimisation, never a hard error.

Unit granularity:
- eslint / mdlint / opengrep -> unit = file, digest = sha256(repo-relative path +
  file content + reporter policy). Path folds in so identical content at two
  paths digests apart (a test-exempt path vs a production path); policy folds in
  so a `.tackbox-reporters` change invalidates.
- erclint -> unit = Go package, digest = sha256(import path + own .go files
  (GoFiles + CgoFiles + TestGoFiles + XTestGoFiles) + transitive in-module deps'
  .go files + go.mod + go.sum + reporter policy). A signature change in package B
  invalidates every in-module package that depends on B; a _test.go edit or a
  policy change invalidates the package.

engines-hash:
- Dev mode digests the engine payload sources (go/, js/, bin/, the eslint
  preset, npm manifest and lockfile), so editing any rule invalidates prior
  markers and stale clean-results can never mask new findings. The
  orchestrator under py/ is deliberately outside the payload. Hermetic
  wheels (step 6) replace this with the bundled-payload digest.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .engines import iter_json_objects
from .hashing import sha256_file, sha256_tree
from .source_set import group_go_packages_by_module, module_relative


CACHE_ROOT_ENV = "TACKBOX_CACHE_HOME"
# v2: the digest scheme changed (non-Go path+policy, Go test/cgo files + policy);
# the version namespace cleanly discards the stale v1 tree.
CACHE_VERSION = "v2"
SOFT_CAP = 20000


def default_cache_root() -> Path:
    """Root under which the `<CACHE_VERSION>/<engines-hash>/...` tree lives."""
    override = os.environ.get(CACHE_ROOT_ENV)
    if override:
        return Path(override) / CACHE_VERSION
    return Path.home() / ".cache" / "tackbox" / CACHE_VERSION


@dataclass(frozen=True)
class CacheKey:
    engines_hash: str
    unit_digest: str
    engine_id: str

    def marker(self, root: Path) -> Path:
        return root / self.engines_hash / f"{self.unit_digest}.{self.engine_id}"


# Engine payload in dev mode: everything that shapes findings, nothing else.
# java/src/main + java/pom.xml (not java/ whole - that would pull in the
# gitignored, per-build target/) so a javalint rule change invalidates markers;
# the built jar is a pure function of those.
_DEV_PAYLOAD = (
    "go",
    "js",
    "bin",
    "eslint.config.preset.js",
    "package.json",
    "package-lock.json",
    "java/src/main",
    "java/pom.xml",
)


def engines_hash_dev(tackbox_root: Path) -> str:
    h = hashlib.sha256()
    h.update(b"dev-payload-v1\n")
    for top in _DEV_PAYLOAD:
        p = tackbox_root / top
        if p.is_file():
            _hash_payload_file(h, top, p)
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    _hash_payload_file(h, f.relative_to(tackbox_root).as_posix(), f)
    return h.hexdigest()


def _hash_payload_file(h, rel: str, path: Path) -> None:
    h.update(rel.encode())
    h.update(b"\t")
    h.update(sha256_file(path).encode())
    h.update(b"\n")


def policy_digest(reporter_pairs: tuple[tuple[str, str, str], ...]) -> str:
    """Hex sha256 over the sorted (file, function, kind) reporter declarations.

    Reason text is not in the pairs, so a reason-only edit does not churn the
    cache. Deterministic and well-defined for an empty policy (no pairs)."""
    h = hashlib.sha256()
    h.update(b"policy-v2\n")
    for file, function, kind in sorted(reporter_pairs):
        h.update(file.encode())
        h.update(b"\t")
        h.update(function.encode())
        h.update(b"\t")
        h.update(kind.encode())
        h.update(b"\n")
    return h.hexdigest()


def non_go_unit_digest(rel_path: str, content_sha: str, policy: str) -> str:
    """Unit digest for a non-Go file: repo-relative path + content sha + policy,
    joined with a separator. Path and policy fold in so identical content at two
    paths, or under a changed `.tackbox-reporters`, never share a marker."""
    h = hashlib.sha256()
    h.update(rel_path.encode())
    h.update(b"\0")
    h.update(content_sha.encode())
    h.update(b"\0")
    h.update(policy.encode())
    return h.hexdigest()


def is_cached(key: CacheKey, root: Path) -> bool:
    """Return True iff a valid marker file exists; re-touch for LRU on hit."""
    p = key.marker(root)
    try:
        if not p.is_file():
            return False
        p.touch()
        return True
    except OSError:
        # no-report: cache is transparent - a miss is never a run failure
        return False


def mark_clean(key: CacheKey, root: Path) -> None:
    """Write empty marker; swallow any OSError so cache never blocks a run."""
    p = key.marker(root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    except OSError:
        # no-report: cache is transparent - a write miss is never a run failure
        pass


def gc_stale_engines(current: str, root: Path) -> None:
    """Drop every `<engines-hash>/` sibling other than `current`."""
    if not root.is_dir():
        return
    for entry in root.iterdir():
        if entry.is_dir() and entry.name != current:
            shutil.rmtree(entry, ignore_errors=True)


def gc_soft_cap(engines_hash: str, cap: int, root: Path) -> None:
    """Trim markers in the current dir when the count exceeds `cap`.

    Sort by mtime ascending; drop from the front until at or under cap. Files
    that vanish under us (concurrent run) are ignored - GC never blocks.
    """
    d = root / engines_hash
    if not d.is_dir():
        return
    markers = [p for p in d.iterdir() if p.is_file()]
    if len(markers) <= cap:
        return

    def _mtime(p: Path) -> float:
        # A marker can vanish between iterdir and stat (concurrent run);
        # treat it as oldest so unlink handles it, never raise.
        try:
            return p.stat().st_mtime
        except OSError:
            # no-report: missing marker sorts as oldest for GC, never fails the run
            return 0.0

    markers.sort(key=_mtime)
    for m in markers[: len(markers) - cap]:
        try:
            m.unlink()
        except OSError:
            # no-report: best-effort GC unlink; a miss is never a run failure
            pass


# -- erclint package digest -----------------------------------------------


class GoListError(RuntimeError):
    """`go list` failed; message carries the module dir and go's stderr."""


def erclint_package_digests(
    repo_root: Path, package_dirs: list[str], policy: str
) -> dict[str, str]:
    """Compute {package_dir: unit_digest} for erclint units.

    Packages are grouped by their nearest enclosing go.mod and digested
    per module: `go list -deps -json` runs with cwd at the module root,
    and the module's own go.mod / go.sum enter the digest, so invalidation
    never crosses a module boundary. Each package's own .go files hash
    together with the .go files of its transitive in-module deps.

    `policy` (the reporter-policy digest) folds into every package digest, so a
    `.tackbox-reporters` change invalidates the packages it can affect.

    Missing / not-a-package / no-enclosing-module entries are dropped from
    the returned map; the caller decides what to do (usually: skip caching
    for that entry).
    """
    if not package_dirs:
        return {}
    groups, _orphans = group_go_packages_by_module(
        package_dirs, lambda d: (repo_root / d / "go.mod").is_file()
    )
    result: dict[str, str] = {}
    for module in sorted(groups):
        result.update(_module_digests(repo_root, module, groups[module], policy))
    return result


def _module_digests(
    repo_root: Path, module: str, package_dirs: list[str], policy: str
) -> dict[str, str]:
    module_dir = repo_root / module
    args = [f"./{module_relative(module, p)}" for p in package_dirs]
    completed = subprocess.run(
        ["go", "list", "-deps", "-json", *args],
        cwd=module_dir,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise GoListError(
            f"go list failed in {module}: {completed.stderr.strip()}"
        )
    pkgs = list(iter_json_objects(completed.stdout))

    our_module = _module_path_from_pkgs(pkgs)
    if our_module is None:
        return {}

    in_module: dict[str, dict] = {}
    for p in pkgs:
        if p.get("Standard"):
            continue
        mod = p.get("Module") or {}
        if mod.get("Path") != our_module:
            continue
        import_path = p["ImportPath"]
        dir_ = Path(p["Dir"])
        # skiptest/ERC008 runs on _test.go (EachTestFile); cgo/xtest belong to
        # the package's analyzed set too. Over-inclusion is safe; under-inclusion
        # was the bug (a _test.go edit left the digest unchanged).
        go_files = (
            list(p.get("GoFiles") or [])
            + list(p.get("CgoFiles") or [])
            + list(p.get("TestGoFiles") or [])
            + list(p.get("XTestGoFiles") or [])
        )
        # Deps is the transitive closure per `go list -json`; filter later.
        deps = set(p.get("Deps") or [])
        in_module[import_path] = {
            "dir": dir_,
            "files": [dir_ / f for f in go_files],
            "deps": deps,
        }
    for info in in_module.values():
        info["deps"] = {d for d in info["deps"] if d in in_module}

    file_digest: dict[Path, str] = {}
    for info in in_module.values():
        for f in info["files"]:
            if f not in file_digest and f.is_file():
                file_digest[f] = sha256_file(f)

    go_mod_digest = _optional_file_digest(module_dir / "go.mod")
    go_sum_digest = _optional_file_digest(module_dir / "go.sum")

    dir_to_import: dict[str, str] = {}
    repo_resolved = repo_root.resolve()
    for import_path, info in in_module.items():
        try:
            rel = info["dir"].resolve().relative_to(repo_resolved)
        except ValueError:
            # no-report: dir outside repo boundary - not ours to digest, skip
            continue
        key = str(rel) if str(rel) != "." else "."
        dir_to_import[key] = import_path

    result: dict[str, str] = {}
    for pkg_dir in package_dirs:
        import_path = dir_to_import.get(pkg_dir)
        if import_path is None:
            continue
        result[pkg_dir] = _package_digest(
            import_path, in_module, file_digest, go_mod_digest, go_sum_digest, policy
        )
    return result


def erclint_import_paths(
    repo_root: Path, package_dirs: list[str]
) -> dict[str, str]:
    """Return {package_dir: import_path}. Used to interpret erclint findings."""
    if not package_dirs:
        return {}
    groups, _orphans = group_go_packages_by_module(
        package_dirs, lambda d: (repo_root / d / "go.mod").is_file()
    )
    repo_resolved = repo_root.resolve()
    result: dict[str, str] = {}
    for module in sorted(groups):
        module_pkgs = groups[module]
        args = [f"./{module_relative(module, p)}" for p in module_pkgs]
        completed = subprocess.run(
            ["go", "list", "-json", *args],
            cwd=repo_root / module,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise GoListError(
                f"go list failed in {module}: {completed.stderr.strip()}"
            )
        for p in iter_json_objects(completed.stdout):
            try:
                rel = Path(p["Dir"]).resolve().relative_to(repo_resolved)
            except ValueError:
                # no-report: dir outside repo boundary - not ours to digest, skip
                continue
            key = str(rel) if str(rel) != "." else "."
            if key in module_pkgs:
                result[key] = p["ImportPath"]
    return result


def _package_digest(
    import_path: str,
    in_module: dict[str, dict],
    file_digest: dict[Path, str],
    go_mod_digest: str,
    go_sum_digest: str,
    policy: str,
) -> str:
    h = hashlib.sha256()
    h.update(import_path.encode())
    h.update(b"\n---self---\n")
    _hash_files(h, in_module[import_path]["files"], file_digest)
    h.update(b"---deps---\n")
    for dep in sorted(in_module[import_path]["deps"]):
        h.update(dep.encode())
        h.update(b"\n")
        _hash_files(h, in_module[dep]["files"], file_digest)
    h.update(b"---go.mod---\n")
    h.update(go_mod_digest.encode())
    h.update(b"\n---go.sum---\n")
    h.update(go_sum_digest.encode())
    h.update(b"\n---policy---\n")
    h.update(policy.encode())
    return h.hexdigest()


def _hash_files(h, files, file_digest: dict[Path, str]) -> None:
    for f in sorted(files):
        digest = file_digest.get(f)
        if digest is None:
            continue
        h.update(f.name.encode())
        h.update(b"\t")
        h.update(digest.encode())
        h.update(b"\n")


def _optional_file_digest(path: Path) -> str:
    if not path.is_file():
        return ""
    return sha256_file(path)


def _module_path_from_pkgs(pkgs: list[dict]) -> str | None:
    for p in pkgs:
        if p.get("Standard"):
            continue
        mod = p.get("Module") or {}
        path = mod.get("Path")
        if path:
            return path
    return None

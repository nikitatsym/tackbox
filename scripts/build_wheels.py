"""Build the tackbox wheels for one platform.

Three platform wheels: thin `tackbox` (built in-tree from py/) and the two
payload wheels the thin store fetches at runtime - `tackbox-engines` (node,
jscpd, vendored node_modules) and `tackbox-opengrep` (the opengrep binary),
each staged in a temp project from assets fetched per engines/manifest.json
and built with `python -m build --wheel` and platform tagging.

Every platform builds on its own host except android-aarch64, which
cross-builds on linux-x86_64: its Rust binaries come from their pinned crates
through the pinned Android NDK (see cargo_build).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

REPO = Path(__file__).resolve().parent.parent
ENGINES_DIR = REPO / "engines"
PY_DIR = REPO / "py"

sys.path.insert(0, str(PY_DIR))
from tackbox.engines import ENGINES, OPENGREP, Payload, payload_tree_sha256  # noqa: E402
from tackbox.hashing import sha256_file, sha256_tree  # noqa: E402
JS_ROOT = REPO
CACHE_DIR = Path(os.environ.get("TACKBOX_BUILD_CACHE", str(Path.home() / ".cache" / "tackbox-build")))


@dataclass
class Platform:
    key: str
    wheel_plat: str
    node: dict | None
    opengrep: dict
    jscpd: dict
    ast_grep: dict | None
    cargo: dict | None


def detect_platform_key() -> str:
    system = sys.platform
    machine = platform.machine().lower()
    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "macos-aarch64"
    if system.startswith("linux"):
        if machine in ("aarch64", "arm64"):
            return "linux-aarch64"
        if machine in ("x86_64", "amd64"):
            return "linux-x86_64"
    if system.startswith("win") or system == "cygwin":
        if machine in ("amd64", "x86_64"):
            return "windows-x86_64"
    raise SystemExit(f"unsupported platform: {system}/{machine}")


def load_manifest() -> dict:
    return json.loads((ENGINES_DIR / "manifest.json").read_text())


def platform_for(manifest: dict, key: str) -> Platform:
    entry = manifest["platforms"][key]
    return Platform(
        key=key,
        wheel_plat=entry["wheel_plat"],
        node=entry.get("node"),
        opengrep=entry["opengrep"],
        jscpd=entry["jscpd"],
        ast_grep=entry.get("ast_grep"),
        cargo=entry.get("cargo"),
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, expected_sha: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"{expected_sha}-{Path(url).name}"
    if dest.is_file() and sha256_file(dest) == expected_sha:
        return dest
    print(f"fetch {url}", file=sys.stderr)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urlopen(url) as r, tmp.open("wb") as f:
        shutil.copyfileobj(r, f)
    got = sha256_file(tmp)
    if got != expected_sha:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"sha256 mismatch for {url}: expected {expected_sha}, got {got}")
    tmp.rename(dest)
    return dest


def extract_member(archive: Path, member: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            with zf.open(member) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
    else:
        with tarfile.open(archive) as tf:
            src = tf.extractfile(member)
            if src is None:
                raise SystemExit(f"member {member} not found in {archive}")
            with src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
    _make_executable(dest)


def copy_binary(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    _make_executable(dest)


def _make_executable(path: Path) -> None:
    st = path.stat().st_mode
    path.chmod(st | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def stage_binary(pl: Platform, entry: dict, dest: Path) -> None:
    """Materialize one manifest binary at dest: built from its crate when the
    entry names a `cargo_bin`, else extracted as `archive_member` from the
    fetched archive, else the fetched file itself."""
    if "cargo_bin" in entry:
        copy_binary(cargo_build(pl, entry), dest)
        return
    archive = fetch(entry["source_url"], entry["archive_sha256"])
    if "archive_member" in entry:
        extract_member(archive, entry["archive_member"], dest)
    else:
        copy_binary(archive, dest)


def extract_zip_tree(archive: Path, dest: Path) -> None:
    """Unzip keeping the Unix modes and symlinks the archive records: the NDK's
    tools need their exec bits, and several of them are symlinks."""
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            target = dest / info.filename
            mode = info.external_attr >> 16
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISLNK(mode):
                target.symlink_to(zf.read(info).decode())
                continue
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            target.chmod(stat.S_IMODE(mode) or 0o644)


def ensure_ndk(ndk: dict) -> Path:
    """The pinned Android NDK, unpacked once into the build cache."""
    root = CACHE_DIR / f"{ndk['archive_sha256']}-ndk"
    if not root.is_dir():
        archive = fetch(ndk["source_url"], ndk["archive_sha256"])
        print(f"unpack {archive.name}", file=sys.stderr)
        part = root.with_name(root.name + ".part")
        shutil.rmtree(part, ignore_errors=True)
        extract_zip_tree(archive, part)
        part.rename(root)
    (top,) = root.iterdir()
    return top


def cargo_build(pl: Platform, entry: dict) -> Path:
    """Build a pinned crate reproducibly for the Android target."""
    if detect_platform_key() != "linux-x86_64":
        raise SystemExit(f"{pl.key} cross-builds only on a linux-x86_64 host (the NDK's)")
    cargo = pl.cargo
    key = sha256_bytes(json.dumps([cargo, entry], sort_keys=True).encode())[:16]
    out = CACHE_DIR / f"{key}-{entry['bin_name']}"
    if out.is_file():
        return out
    rust, target = cargo["rust"], cargo["target"]
    subprocess.run(
        ["rustup", "toolchain", "install", rust, "--profile", "minimal", "--target", target],
        check=True, stdout=sys.stderr,
    )
    bin_dir = ensure_ndk(cargo["ndk"]) / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin"
    clang = bin_dir / f"{target}{cargo['android_api']}-clang"
    crate = fetch(entry["source_url"], entry["archive_sha256"])
    work = CACHE_DIR / f"{key}-build"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        with tarfile.open(crate) as tf:
            tf.extractall(work, filter="data")
        (src,) = work.iterdir()
        cargo_home = Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo")
        remap = ((src, "/build"), (cargo_home, "/cargo"))
        var = target.replace("-", "_")
        env = {
            **os.environ,
            "RUSTUP_TOOLCHAIN": rust,
            "CARGO_PROFILE_RELEASE_STRIP": "symbols",
            f"CARGO_TARGET_{var.upper()}_LINKER": str(clang),
            f"CARGO_TARGET_{var.upper()}_RUSTFLAGS": " ".join(
                f"--remap-path-prefix={path}={to}" for path, to in remap
            ),
            f"CC_{var}": str(clang),
            f"CXX_{var}": f"{clang}++",
            f"AR_{var}": str(bin_dir / "llvm-ar"),
            f"CFLAGS_{var}": " ".join(f"-ffile-prefix-map={path}={to}" for path, to in remap),
        }
        print(f"cargo build {entry['cargo_bin']} {entry['version']} -> {target}", file=sys.stderr)
        subprocess.run(
            ["cargo", "build", "--release", "--locked", "--target", target, "--bin", entry["cargo_bin"]],
            cwd=src, env=env, check=True, stdout=sys.stderr,
        )
        part = out.with_name(out.name + ".part")
        shutil.copyfile(src / "target" / target / "release" / entry["cargo_bin"], part)
        part.rename(out)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


def build_javalint_jar() -> Path:
    """Build the shaded, platform-independent javalint.jar via maven.

    The jar rides in the thin wheel (one build per platform run; the bytes
    are arch-independent). Tests already ran in dev.py check, so packaging
    skips them.
    """
    print("mvn package javalint.jar", file=sys.stderr)
    mvn = shutil.which("mvn") or "mvn"
    subprocess.run(
        [mvn, "-q", "-B", "-f", str(REPO / "java" / "pom.xml"), "-DskipTests", "package"],
        cwd=REPO, check=True, stdout=sys.stderr,
    )
    jar = REPO / "java" / "target" / "javalint.jar"
    if not jar.is_file():
        raise SystemExit(f"expected shaded jar at {jar} after mvn package")
    return jar


def stage_engine_licenses(thin_root: Path, engine: str) -> list[dict]:
    source = ENGINES_DIR / "licenses" / engine
    destination = thin_root / "third_party" / "licenses" / engine
    shutil.copytree(source, destination)
    inventory = json.loads((source / "inventory.json").read_text())
    entries = []
    for component in inventory["components"]:
        license_file = destination / component["license_path"]
        if not license_file.is_file():
            raise SystemExit(f"missing license for {component['id']}: {license_file}")
        notice_path = component.get("notice_path")
        if notice_path and not (destination / notice_path).is_file():
            raise SystemExit(f"missing notice for {component['id']}: {notice_path}")
        entries.append({
            "id": f"{engine}-dependency/{component['id']}",
            "kind": "dependency",
            "version": component["version"],
            "sha256": sha256_file(license_file),
            "path": f"tackbox/{license_file.relative_to(thin_root).as_posix()}",
            "license": component["license"],
            "license_path": f"tackbox/{license_file.relative_to(thin_root).as_posix()}",
            "source": component["source"],
            "notice_path": f"tackbox/{(destination / notice_path).relative_to(thin_root).as_posix()}"
                if notice_path else "",
        })
    return entries


def _stage_project(
    staging_root: Path, project_dir: Path, project_files: tuple[str, ...], package: str
) -> Path:
    for name in project_files:
        shutil.copy2(project_dir / name, staging_root / name)
    pkg_root = staging_root / "src" / package
    pkg_root.mkdir(parents=True)
    shutil.copy2(project_dir / "src" / package / "__init__.py", pkg_root / "__init__.py")
    return pkg_root


def _cleanup_staging_root(staging_root: Path) -> None:
    shutil.rmtree(staging_root, ignore_errors=True)


def _reset_generated_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def prepare_fat(
    pl: Platform, manifest: dict, engines_version: str, fat_root: Path
) -> list[dict]:
    (fat_root / "bin").mkdir(parents=True)
    (fat_root / "third_party" / "licenses").mkdir(parents=True)

    entries: list[dict] = []

    # Android requires a system Node.js because Linux binaries cannot run there.
    if pl.node is not None:
        node_bin = fat_root / "bin" / pl.node["bin_name"]
        stage_binary(pl, pl.node, node_bin)
        node_notice = manifest["licenses"]["node"]
        (fat_root / "third_party" / "licenses" / "node.LICENSE.txt").write_text(node_notice["notice"] + "\n")
        entries.append({
            "id": "node",
            "kind": "runtime",
            "version": pl.node["version"],
            "source_url": pl.node["source_url"],
            "archive_sha256": pl.node["archive_sha256"],
            "sha256": sha256_file(node_bin),
            "license": node_notice["spdx"],
            "license_path": "tackbox_engines/third_party/licenses/node.LICENSE.txt",
            "path": f"tackbox_engines/bin/{pl.node['bin_name']}",
        })

    jscpd_bin = fat_root / "bin" / pl.jscpd["bin_name"]
    stage_binary(pl, pl.jscpd, jscpd_bin)
    jscpd_notice = manifest["licenses"]["jscpd"]
    (fat_root / "third_party" / "licenses" / "jscpd.LICENSE.txt").write_text(jscpd_notice["notice"] + "\n")
    entries.append({
        "id": "jscpd",
        "kind": "binary",
        "version": pl.jscpd["version"],
        "source_url": pl.jscpd["source_url"],
        "archive_sha256": pl.jscpd["archive_sha256"],
        "sha256": sha256_file(jscpd_bin),
        "license": jscpd_notice["spdx"],
        "license_path": "tackbox_engines/third_party/licenses/jscpd.LICENSE.txt",
        "path": f"tackbox_engines/bin/{pl.jscpd['bin_name']}",
    })

    vendor_src = ENGINES_DIR / "vendor"
    lock_src = vendor_src / "package-lock.json"
    if not lock_src.is_file():
        raise SystemExit(
            "engines/vendor/package-lock.json missing; the vendored payload "
            "must be reproducible. Generate with `npm install "
            "--package-lock-only` in engines/vendor/ and commit."
        )
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        shutil.copyfile(vendor_src / "package.json", td_path / "package.json")
        shutil.copyfile(lock_src, td_path / "package-lock.json")
        print("npm ci (vendored deps)", file=sys.stderr)
        # On Windows npm is `npm.cmd` (batch); subprocess.run does not resolve
        # PATHEXT, so pass the full path from shutil.which.
        npm = shutil.which("npm") or "npm"
        subprocess.run(
            [npm, "ci", "--omit=dev", "--no-audit", "--no-fund", "--loglevel=error"],
            cwd=td_path, check=True, stdout=sys.stderr,
        )
        dest_vendor = fat_root / "vendor"
        dest_vendor.mkdir(parents=True)
        shutil.copyfile(td_path / "package.json", dest_vendor / "package.json")
        shutil.copyfile(td_path / "package-lock.json", dest_vendor / "package-lock.json")
        shutil.copytree(td_path / "node_modules", dest_vendor / "node_modules")

    # setuptools package-data globs never match dot-prefixed path components:
    # dot-named files would silently stay out of the wheel while still being
    # part of the staged tree hashes.
    prune_dot_paths(dest_vendor)

    for entry_name, meta in manifest["npm_deps"]["top_level"].items():
        pkg_dir = (fat_root / "vendor" / "node_modules" / entry_name).resolve()
        pkg_json = pkg_dir / "package.json"
        if not pkg_json.is_file():
            raise SystemExit(f"vendored npm dep missing after install: {entry_name}")
        installed = json.loads(pkg_json.read_text())
        entries.append({
            "id": entry_name,
            "kind": meta["kind"],
            "version": installed.get("version", ""),
            "source_url": f"https://registry.npmjs.org/{entry_name}/-/{entry_name.replace('@', '').replace('/', '-')}-{installed.get('version', '')}.tgz",
            "constraint": meta["constraint"],
            "sha256": sha256_tree(pkg_dir),
            "license": meta["license"],
            "license_path": f"tackbox_engines/vendor/node_modules/{entry_name}/LICENSE"
                if (pkg_dir / "LICENSE").exists() else "",
            "path": f"tackbox_engines/vendor/node_modules/{entry_name}",
        })

    # Top-level npm entries cover only their own dirs; transitive deps are
    # hoisted siblings. One tree entry keeps the whole vendored payload
    # verifiable and in the cache key.
    entries.append({
        "id": "vendor-tree",
        "kind": "vendor",
        "version": engines_version,
        "sha256": sha256_tree(fat_root / "vendor"),
        "license": "various (see per-package entries)",
        "license_path": "",
        "path": "tackbox_engines/vendor",
    })

    return entries


def prepare_opengrep(pl: Platform, manifest: dict, og_root: Path) -> list[dict]:
    (og_root / "bin").mkdir(parents=True)
    (og_root / "third_party" / "licenses").mkdir(parents=True)
    # The static core's opengrep CLI avoids the regular CLI's libc dependency.
    opengrep_bin = og_root / "bin" / pl.opengrep["bin_name"]
    stage_binary(pl, pl.opengrep, opengrep_bin)
    og_notice = manifest["licenses"]["opengrep"]
    (og_root / "third_party" / "licenses" / "opengrep.LICENSE.txt").write_text(og_notice["notice"] + "\n")
    return [{
        "id": "opengrep",
        "kind": "binary",
        "version": pl.opengrep["version"],
        "source_url": pl.opengrep["source_url"],
        "archive_sha256": pl.opengrep["archive_sha256"],
        "sha256": sha256_file(opengrep_bin),
        "license": og_notice["spdx"],
        "license_path": "tackbox_opengrep/third_party/licenses/opengrep.LICENSE.txt",
        "path": f"tackbox_opengrep/bin/{pl.opengrep['bin_name']}",
    }]


@dataclass(frozen=True)
class PayloadProject:
    payload: Payload
    project_dir: Path
    project_files: tuple[str, ...]
    version_env: str  # env var the project's setup.py reads its version from


ENGINES_PROJECT = PayloadProject(
    ENGINES, ENGINES_DIR,
    ("pyproject.toml", "setup.py", "VERSION", "manifest.json", "lock.json"),
    "TACKBOX_ENGINES_VERSION",
)
OPENGREP_PROJECT = PayloadProject(
    OPENGREP, ENGINES_DIR / "opengrep",
    ("pyproject.toml", "setup.py", "VERSION"),
    "TACKBOX_OPENGREP_VERSION",
)


def build_payload_wheel(
    project: PayloadProject, version: str, pl: Platform, outdir: Path, prepare
) -> tuple[Path, list[dict], dict]:
    """Stage one payload project in a temp dir, fill its package via
    `prepare(pkg_root) -> entries`, and build the platform wheel. Returns the
    wheel, its engines.json entries, and the pin the thin store resolves it by."""
    payload = project.payload
    stage = Path(tempfile.mkdtemp(prefix=f"{payload.project}-"))
    try:
        pkg_root = _stage_project(stage, project.project_dir, project.project_files, payload.package)
        entries = prepare(pkg_root)
        wheel = build_wheel(stage, pl.wheel_plat, outdir, env_extras={project.version_env: version})
        print(f"{payload.project} wheel: {wheel.name}", file=sys.stderr)
        verify_wheel_payload(wheel, pkg_root, payload.package)
    finally:
        _cleanup_staging_root(stage)
    # The store fetches this exact wheel by name and verifies the unpacked
    # payload against store_sha256. No wheel-file sha pin: a rebuild of a
    # published payload version is not zip-reproducible and PyPI keeps the
    # first upload (skip-existing), so container bytes legitimately differ.
    pin = {
        "project": payload.project,
        "version": version,
        "wheel": wheel.name,
        "store_sha256": payload_tree_sha256(wheel, payload.package),
    }
    return wheel, entries, pin


def prepare_thin(
    pl: Platform,
    manifest: dict,
    payload_entries: list[dict],
    version: str,
    pins: dict[str, dict],
) -> tuple[Path, list[dict], str]:
    thin_root = PY_DIR / "tackbox"
    _reset_generated_dir(thin_root / "bin")
    _reset_generated_dir(thin_root / "rules")
    _reset_generated_dir(thin_root / "third_party")
    (thin_root / "bin").mkdir(parents=True)
    (thin_root / "rules" / "bin").mkdir(parents=True)
    (thin_root / "third_party" / "licenses").mkdir(parents=True)

    exe_suffix = ".exe" if pl.key == "windows-x86_64" else ""
    # GOOS=linux probes executables with faccessat2, which Android kills via SIGSYS.
    goos = {
        "linux-x86_64": "linux",
        "linux-aarch64": "linux",
        "android-aarch64": "android",
        "macos-aarch64": "darwin",
        "windows-x86_64": "windows",
    }[pl.key]
    goarch = {
        "linux-x86_64": "amd64",
        "linux-aarch64": "arm64",
        "android-aarch64": "arm64",
        "macos-aarch64": "arm64",
        "windows-x86_64": "amd64",
    }[pl.key]

    thin_entries: list[dict] = []
    thin_entries.extend(stage_engine_licenses(thin_root, "go"))
    thin_entries.extend(stage_engine_licenses(thin_root, "javalint"))

    toolchain = "go" + next(line.split()[1] for line in (REPO / "go.mod").read_text().splitlines()
                           if line.startswith("go "))
    for cmd in ("erclint", "erclint-opengrep", "tackbox-jscpd"):
        out = thin_root / "bin" / f"{cmd}{exe_suffix}"
        print(f"go build {cmd} -> {out.relative_to(REPO)}", file=sys.stderr)
        env = {**os.environ, "GOOS": goos, "GOARCH": goarch, "CGO_ENABLED": "0", "GOTOOLCHAIN": toolchain}
        subprocess.run(
            [
                "go", "build",
                "-trimpath",
                "-ldflags", f"-s -w -X main.version={version}",
                "-o", str(out),
                f"./go/cmd/{cmd}",
            ],
            cwd=REPO, env=env, check=True, stdout=sys.stderr,
        )
        thin_entries.append({
            "id": cmd,
            "kind": "binary",
            "version": version,
            "sha256": sha256_file(out),
            "path": f"tackbox/bin/{cmd}{exe_suffix}",
            "license": "MIT AND BSD-3-Clause",
            "license_path": "tackbox/third_party/licenses/go/NOTICE.txt",
        })

    # ast-grep-cli has no Android wheel, so the pinned grammar is bundled.
    if pl.ast_grep is not None:
        ast_grep = thin_root / "bin" / pl.ast_grep["bin_name"]
        stage_binary(pl, pl.ast_grep, ast_grep)
        notice = manifest["licenses"]["ast-grep"]
        (thin_root / "third_party" / "licenses" / "ast-grep.LICENSE.txt").write_text(notice["notice"] + "\n")
        thin_entries.append({
            "id": "ast-grep",
            "kind": "binary",
            "version": pl.ast_grep["version"],
            "source_url": pl.ast_grep["source_url"],
            "archive_sha256": pl.ast_grep["archive_sha256"],
            "sha256": sha256_file(ast_grep),
            "path": f"tackbox/bin/{pl.ast_grep['bin_name']}",
            "license": notice["spdx"],
            "license_path": "tackbox/third_party/licenses/ast-grep.LICENSE.txt",
        })

    # javalint is a JVM engine: one platform-independent shaded jar run via
    # the system `java` toolchain. No exe suffix; doctor checksums it like
    # any binary.
    jar_src = build_javalint_jar()
    jar_dest = thin_root / "bin" / "javalint.jar"
    shutil.copyfile(jar_src, jar_dest)
    thin_entries.append({
        "id": "javalint",
        "kind": "binary",
        "version": version,
        "sha256": sha256_file(jar_dest),
        "path": "tackbox/bin/javalint.jar",
        "license": "MIT AND Apache-2.0 AND BSD-3-Clause AND LicenseRef-PMD",
        "license_path": "tackbox/third_party/licenses/javalint/NOTICE.txt",
    })

    # Rules layout mirrors the dev tree so the bundled node scripts keep
    # their relative require paths (`../eslint.config.preset.js` etc).
    (thin_root / "rules" / "opengrep").mkdir(parents=True, exist_ok=True)
    (thin_root / "rules" / "js" / "rules").mkdir(parents=True, exist_ok=True)
    (thin_root / "rules" / "js" / "markdownlint-rules").mkdir(parents=True, exist_ok=True)

    for yaml in (REPO / "go" / "cmd" / "erclint-opengrep" / "rules").glob("*.yaml"):
        shutil.copyfile(yaml, thin_root / "rules" / "opengrep" / yaml.name)

    shutil.copyfile(REPO / "eslint.config.preset.js", thin_root / "rules" / "eslint.config.preset.js")
    shutil.copyfile(REPO / "js" / "eslint-plugin.js", thin_root / "rules" / "js" / "eslint-plugin.js")
    for r in (REPO / "js" / "rules").iterdir():
        if r.is_file():
            shutil.copyfile(r, thin_root / "rules" / "js" / "rules" / r.name)
    for r in (REPO / "js" / "markdownlint-rules").iterdir():
        if r.is_file():
            shutil.copyfile(r, thin_root / "rules" / "js" / "markdownlint-rules" / r.name)
    shutil.copyfile(REPO / "bin" / "tackbox-eslint.js", thin_root / "rules" / "bin" / "tackbox-eslint.js")
    shutil.copyfile(REPO / "bin" / "tackbox-mdlint.js", thin_root / "rules" / "bin" / "tackbox-mdlint.js")

    license_src = REPO / "LICENSE"
    if license_src.is_file():
        shutil.copyfile(license_src, thin_root / "third_party" / "licenses" / "tackbox.LICENSE.txt")

    for f in (thin_root / "rules").rglob("*"):
        if f.is_file():
            thin_entries.append({
                "id": f"rules/{f.relative_to(thin_root / 'rules').as_posix()}",
                "kind": "rules",
                "version": version,
                "sha256": sha256_file(f),
                "path": f"tackbox/{f.relative_to(thin_root).as_posix()}",
                "license": "MIT",
                "license_path": "tackbox/third_party/licenses/tackbox.LICENSE.txt",
            })

    all_entries = payload_entries + thin_entries
    all_entries.extend(python_rule_entries(PY_DIR))
    payload_sha = compute_payload_sha(all_entries)

    engines_json = {
        "schema": 1,
        "payload_sha256": payload_sha,
        # Both payload wheels carry this platform; the store refuses a
        # wrong-arch install before downloading.
        "platform": pl.key,
        "wheel_plat": pl.wheel_plat,
        "payloads": pins,
        "node": "bundled" if pl.node is not None else "system",
        "engines": all_entries,
    }
    (thin_root / "engines.json").write_text(json.dumps(engines_json, indent=2, sort_keys=True))

    __init__ = thin_root / "__init__.py"
    __init__.write_text(f'__version__ = "{version}"\n')

    return thin_root, thin_entries, payload_sha


def prune_dot_paths(root: Path) -> None:
    for p in sorted(root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        if not p.exists():
            continue
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)


def verify_wheel_payload(wheel: Path, pkg_root: Path, pkg_name: str) -> None:
    """Every staged payload file must land in the wheel - fail the build,
    not the consumer's doctor, when packaging drops something."""
    with zipfile.ZipFile(wheel) as zf:
        packed = {n for n in zf.namelist() if n.startswith(f"{pkg_name}/")}
    staged = {
        f"{pkg_name}/{f.relative_to(pkg_root).as_posix()}"
        for f in pkg_root.rglob("*")
        if f.is_file() and "__pycache__" not in f.parts
    }
    missing = sorted(staged - packed)
    if missing:
        raise SystemExit(
            f"wheel {wheel.name} is missing {len(missing)} staged files, "
            f"first: {missing[:5]}"
        )


def python_rule_entries(py_dir: Path) -> list[dict]:
    entries = [
        {
            "id": f"pyrules/{source.name}", "kind": "rules",
            "path": f"tackbox/pyrules/{source.name}", "sha256": sha256_file(source),
            "license": "MIT", "license_path": "tackbox/third_party/licenses/tackbox.LICENSE.txt",
        }
        for source in sorted((py_dir / "tackbox" / "pyrules").glob("*.py"))
    ]
    # Dependency pins change findings even when the plugin source is unchanged.
    entries.append({"id": "pyrules/dependency-pins", "kind": "dependency",
                    "sha256": sha256_file(py_dir / "setup.py")})
    return entries


def compute_payload_sha(entries: list[dict]) -> str:
    h = hashlib.sha256()
    for entry in sorted(entries, key=lambda e: (e.get("id", ""), e.get("path", ""))):
        h.update(entry.get("id", "").encode("utf-8"))
        h.update(b"\0")
        h.update(entry.get("path", "").encode("utf-8"))
        h.update(b"\0")
        h.update(entry.get("sha256", "").encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def build_wheel(project_dir: Path, plat_tag: str, outdir: Path, env_extras: dict) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    project_label = (
        project_dir.relative_to(REPO)
        if project_dir.is_relative_to(REPO)
        else project_dir
    )
    print(f"build wheel in {project_label} -> {plat_tag}", file=sys.stderr)
    env = {**os.environ, **env_extras}
    stage = project_dir / "build"
    if stage.exists():
        shutil.rmtree(stage)
    subprocess.run(
        [
            "uvx", "--with", "setuptools>=68", "--with", "wheel",
            "--from", "build",
            "python", "-m", "build",
            "--wheel",
            "--outdir", str(outdir),
            "--no-isolation",
            f"--config-setting=--build-option=--plat-name={plat_tag}",
            "--config-setting=--build-option=--python-tag=py3",
        ],
        cwd=project_dir, env=env, check=True, stdout=sys.stderr,
    )
    wheels = sorted(outdir.glob("*.whl"), key=lambda p: p.stat().st_mtime)
    return wheels[-1]


def restore_thin_tree() -> None:
    """Drop everything prepare_thin materialized into the source tree: dev
    `uv run --directory py` rebuilds the local package, and leftover engine
    binaries would ride into every cached build (gigabytes of uv cache)."""
    pkg = PY_DIR / "tackbox"
    for sub in ("bin", "rules", "third_party"):
        _reset_generated_dir(pkg / sub)
    (pkg / "engines.json").unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=os.environ.get("TACKBOX_VERSION", "0.0.0"),
                    help="version for all wheels")
    ap.add_argument("--engines-version", default=None,
                    help="tackbox-engines wheel version (defaults to --version)")
    ap.add_argument("--opengrep-version", default=None,
                    help="tackbox-opengrep wheel version (defaults to --version)")
    ap.add_argument("--outdir", default=str(REPO / "dist"))
    ap.add_argument("--platform", default=None,
                    help="platform key override (linux-x86_64|linux-aarch64|macos-aarch64|"
                         "windows-x86_64|android-aarch64)")
    args = ap.parse_args()

    plat_key = args.platform or detect_platform_key()
    manifest = load_manifest()
    if plat_key not in manifest["platforms"]:
        raise SystemExit(f"platform {plat_key} not in manifest")
    pl = platform_for(manifest, plat_key)

    version = args.version
    engines_version = args.engines_version or version
    opengrep_version = args.opengrep_version or version
    # `python -m build --outdir` resolves against subprocess cwd, not ours.
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    fat_wheel, engines_entries, engines_pin = build_payload_wheel(
        ENGINES_PROJECT, engines_version, pl, outdir,
        lambda root: prepare_fat(pl, manifest, engines_version, root),
    )
    opengrep_wheel, opengrep_entries, opengrep_pin = build_payload_wheel(
        OPENGREP_PROJECT, opengrep_version, pl, outdir,
        lambda root: prepare_opengrep(pl, manifest, root),
    )

    prepare_thin(
        pl, manifest, engines_entries + opengrep_entries, version,
        {ENGINES.key: engines_pin, OPENGREP.key: opengrep_pin},
    )

    thin_wheel = build_wheel(PY_DIR, pl.wheel_plat, outdir, env_extras={"TACKBOX_VERSION": version})
    print(f"thin wheel: {thin_wheel.name}", file=sys.stderr)
    verify_wheel_payload(thin_wheel, PY_DIR / "tackbox", "tackbox")

    restore_thin_tree()

    print(json.dumps({
        "platform": plat_key,
        "wheel_plat": pl.wheel_plat,
        "version": version,
        "engines_version": engines_version,
        "opengrep_version": opengrep_version,
        "fat": str(fat_wheel),
        "opengrep": str(opengrep_wheel),
        "thin": str(thin_wheel),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

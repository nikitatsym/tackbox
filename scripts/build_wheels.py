"""Build tackbox thin/fat wheels for the current platform.

Populates engines/src/tackbox_engines/ and py/tackbox/ from local
sources plus assets fetched per engines/manifest.json, then invokes
`python -m build --wheel` for each package with platform tagging.
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
from tackbox.cache import sha256_tree  # noqa: E402
from tackbox.engines import engines_payload_tree_sha256  # noqa: E402
from tackbox.hashing import sha256_file  # noqa: E402
JS_ROOT = REPO
CACHE_DIR = Path(os.environ.get("TACKBOX_BUILD_CACHE", str(Path.home() / ".cache" / "tackbox-build")))


@dataclass
class Platform:
    key: str
    wheel_plat: str
    node: dict
    opengrep: dict
    jscpd: dict


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
        node=entry["node"],
        opengrep=entry["opengrep"],
        jscpd=entry["jscpd"],
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


def extract_node_binary(archive: Path, member: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if archive.suffix in (".xz",) or archive.name.endswith(".tar.xz"):
        with tarfile.open(archive, "r:xz") as tf:
            src = tf.extractfile(member)
            if src is None:
                raise SystemExit(f"member {member} not found in {archive}")
            with dest.open("wb") as out:
                shutil.copyfileobj(src, out)
    elif archive.suffix == ".zip" or archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            with zf.open(member) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
    else:
        raise SystemExit(f"unsupported archive: {archive.name}")
    st = dest.stat().st_mode
    dest.chmod(st | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def copy_binary(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    st = dest.stat().st_mode
    dest.chmod(st | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_javalint_jar() -> Path:
    """Build the shaded, platform-independent javalint.jar via maven.

    The jar rides in the thin wheel (one build per platform run; the bytes are
    arch-independent). Tests already ran in dev.py check, so packaging skips
    them here.
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


def wipe_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def prepare_fat(pl: Platform, manifest: dict, engines_version: str) -> tuple[Path, list[dict]]:
    fat_root = ENGINES_DIR / "src" / "tackbox_engines"
    wipe_dir(fat_root / "bin")
    wipe_dir(fat_root / "vendor")
    wipe_dir(fat_root / "third_party")

    (fat_root / "bin").mkdir(parents=True)
    (fat_root / "third_party" / "licenses").mkdir(parents=True)

    entries: list[dict] = []

    node_archive = fetch(pl.node["source_url"], pl.node["archive_sha256"])
    node_bin = fat_root / "bin" / pl.node["bin_name"]
    extract_node_binary(node_archive, pl.node["archive_member"], node_bin)
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

    opengrep_archive = fetch(pl.opengrep["source_url"], pl.opengrep["archive_sha256"])
    opengrep_bin = fat_root / "bin" / pl.opengrep["bin_name"]
    if pl.opengrep["source_url"].endswith(".tar.gz"):
        with tarfile.open(opengrep_archive, "r:gz") as tf:
            members = [m for m in tf.getmembers() if m.name.endswith("opengrep-core") and m.isfile()]
            if not members:
                raise SystemExit("opengrep-core binary not found in tarball")
            src = tf.extractfile(members[0])
            with opengrep_bin.open("wb") as out:
                shutil.copyfileobj(src, out)
    else:
        copy_binary(opengrep_archive, opengrep_bin)
    st = opengrep_bin.stat().st_mode
    opengrep_bin.chmod(st | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    og_notice = manifest["licenses"]["opengrep"]
    (fat_root / "third_party" / "licenses" / "opengrep.LICENSE.txt").write_text(og_notice["notice"] + "\n")
    entries.append({
        "id": "opengrep",
        "kind": "binary",
        "version": pl.opengrep["version"],
        "source_url": pl.opengrep["source_url"],
        "archive_sha256": pl.opengrep["archive_sha256"],
        "sha256": sha256_file(opengrep_bin),
        "license": og_notice["spdx"],
        "license_path": "tackbox_engines/third_party/licenses/opengrep.LICENSE.txt",
        "path": f"tackbox_engines/bin/{pl.opengrep['bin_name']}",
    })

    # jscpd rides as an npm platform-package tarball (.tgz); the binary sits at a
    # fixed member path inside, so extract it by name like the node archive.
    jscpd_archive = fetch(pl.jscpd["source_url"], pl.jscpd["archive_sha256"])
    jscpd_bin = fat_root / "bin" / pl.jscpd["bin_name"]
    member = pl.jscpd["archive_member"]
    with tarfile.open(jscpd_archive, "r:gz") as tf:
        src = tf.extractfile(member)
        if src is None:
            raise SystemExit(f"member {member} not found in {jscpd_archive}")
        with jscpd_bin.open("wb") as out:
            shutil.copyfileobj(src, out)
    st = jscpd_bin.stat().st_mode
    jscpd_bin.chmod(st | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
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

    # setuptools package-data globs never match dot-prefixed path
    # components, so anything dot-named would silently stay out of the
    # wheel while still being part of the staged tree hashes.
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

    # Top-level npm entries cover their own dirs only; transitive deps are
    # hoisted siblings. One tree entry makes the whole vendored payload
    # verifiable and part of the cache key.
    entries.append({
        "id": "vendor-tree",
        "kind": "vendor",
        "version": engines_version,
        "sha256": sha256_tree(fat_root / "vendor"),
        "license": "various (see per-package entries)",
        "license_path": "",
        "path": "tackbox_engines/vendor",
    })

    return fat_root, entries


def prepare_thin(
    pl: Platform,
    engines_entries: list[dict],
    version: str,
    engines_version: str,
    fat_wheel: Path,
) -> tuple[Path, list[dict], str]:
    thin_root = PY_DIR / "tackbox"
    wipe_dir(thin_root / "bin")
    wipe_dir(thin_root / "rules")
    wipe_dir(thin_root / "third_party")
    (thin_root / "bin").mkdir(parents=True)
    (thin_root / "rules" / "bin").mkdir(parents=True)
    (thin_root / "third_party" / "licenses").mkdir(parents=True)

    exe_suffix = ".exe" if pl.key == "windows-x86_64" else ""
    goos = {
        "linux-x86_64": "linux",
        "linux-aarch64": "linux",
        "macos-aarch64": "darwin",
        "windows-x86_64": "windows",
    }[pl.key]
    goarch = {
        "linux-x86_64": "amd64",
        "linux-aarch64": "arm64",
        "macos-aarch64": "arm64",
        "windows-x86_64": "amd64",
    }[pl.key]

    thin_entries: list[dict] = []

    for cmd in ("erclint", "erclint-opengrep", "tackbox-jscpd"):
        out = thin_root / "bin" / f"{cmd}{exe_suffix}"
        print(f"go build {cmd} -> {out.relative_to(REPO)}", file=sys.stderr)
        env = {**os.environ, "GOOS": goos, "GOARCH": goarch, "CGO_ENABLED": "0"}
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
            "license": "MIT",
            "license_path": "tackbox/third_party/licenses/tackbox.LICENSE.txt",
        })

    # javalint is a JVM engine: one platform-independent shaded jar, run via the
    # system `java` toolchain. No exe suffix; doctor checksums it like any binary.
    jar_src = build_javalint_jar()
    jar_dest = thin_root / "bin" / "javalint.jar"
    shutil.copyfile(jar_src, jar_dest)
    thin_entries.append({
        "id": "javalint",
        "kind": "binary",
        "version": version,
        "sha256": sha256_file(jar_dest),
        "path": "tackbox/bin/javalint.jar",
        "license": "MIT",
        "license_path": "tackbox/third_party/licenses/tackbox.LICENSE.txt",
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

    all_entries = engines_entries + thin_entries
    payload_sha = compute_payload_sha(all_entries)

    engines_json = {
        "schema": 1,
        "engines_version": engines_version,
        "payload_sha256": payload_sha,
        "platform": pl.key,
        "wheel_plat": pl.wheel_plat,
        # The store fetches this exact fat wheel by name and verifies the
        # unpacked payload against store_sha256. No wheel-file sha pin: a
        # rebuild of a published engines version is not zip-reproducible and
        # PyPI keeps the first upload (skip-existing), so container bytes
        # legitimately differ. platform lets the store refuse a wrong-arch
        # wheel before downloading.
        "fat_wheel": {
            "platform": pl.key,
            "wheel": fat_wheel.name,
        },
        "store_sha256": engines_payload_tree_sha256(fat_wheel),
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
    print(f"build wheel in {project_dir.relative_to(REPO)} -> {plat_tag}", file=sys.stderr)
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
        wipe_dir(pkg / sub)
    (pkg / "engines.json").unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=os.environ.get("TACKBOX_VERSION", "0.0.0"),
                    help="version for both wheels")
    ap.add_argument("--engines-version", default=None,
                    help="fat wheel version (defaults to --version)")
    ap.add_argument("--outdir", default=str(REPO / "dist"))
    ap.add_argument("--platform", default=None,
                    help="platform key override (linux-x86_64|linux-aarch64|macos-aarch64|windows-x86_64)")
    args = ap.parse_args()

    plat_key = args.platform or detect_platform_key()
    manifest = load_manifest()
    if plat_key not in manifest["platforms"]:
        raise SystemExit(f"platform {plat_key} not in manifest")
    pl = platform_for(manifest, plat_key)

    version = args.version
    engines_version = args.engines_version or version
    # `python -m build --outdir` resolves against subprocess cwd, not ours.
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    _, engines_entries = prepare_fat(pl, manifest, engines_version)

    fat_wheel = build_wheel(
        ENGINES_DIR,
        pl.wheel_plat,
        outdir,
        env_extras={"TACKBOX_ENGINES_VERSION": engines_version},
    )
    print(f"fat wheel: {fat_wheel.name}", file=sys.stderr)
    verify_wheel_payload(
        fat_wheel, ENGINES_DIR / "src" / "tackbox_engines", "tackbox_engines"
    )

    prepare_thin(pl, engines_entries, version, engines_version, fat_wheel)

    thin_wheel = build_wheel(
        PY_DIR,
        pl.wheel_plat,
        outdir,
        env_extras={
            "TACKBOX_VERSION": version,
            "TACKBOX_ENGINES_VERSION": engines_version,
        },
    )
    print(f"thin wheel: {thin_wheel.name}", file=sys.stderr)
    verify_wheel_payload(thin_wheel, PY_DIR / "tackbox", "tackbox")

    restore_thin_tree()

    print(json.dumps({
        "platform": plat_key,
        "wheel_plat": pl.wheel_plat,
        "version": version,
        "engines_version": engines_version,
        "fat": str(fat_wheel),
        "thin": str(thin_wheel),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

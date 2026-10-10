"""Session-scoped e2e: build thin/fat wheels, install the thin wheel alone
into a fresh venv, and drive tackbox against the fixture repo with the
engine payload supplied via TACKBOX_ENGINES_DIR.

F6: the engine binaries come from the machine store, fetched from PyPI at
runtime. A CI/e2e run can't fetch an unpublished version, so
TACKBOX_ENGINES_DIR points at the unpacked fat wheel - the store's override
path - which also keeps the run offline.

Fixture materialization lives in scripts/materialize_fixture.py so the same
seeded violations drive both this session and the wheels CI matrix.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tackbox import engines
from tackbox.hashing import sha256_tree

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from build_wheels import compute_payload_sha, python_rule_entries

REPO = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO / "scripts" / "build_wheels.py"
MATERIALIZE_SCRIPT = REPO / "scripts" / "materialize_fixture.py"


def test_hermetic_hash_changes_with_only_python_rule_or_dependency_pin(tmp_path):
    pyrules = tmp_path / 'tackbox' / 'pyrules'
    pyrules.mkdir(parents=True)
    checker = pyrules / 'checker.py'
    checker.write_text('first rule\n')
    setup = tmp_path / 'setup.py'
    setup.write_text('flake8==7.3.0; pyflakes==3.4.0\n')
    before = compute_payload_sha(python_rule_entries(tmp_path))
    checker.write_text('second rule\n')
    after_rule = compute_payload_sha(python_rule_entries(tmp_path))
    assert after_rule != before
    setup.write_text('flake8==7.3.0; pyflakes==next\n')
    assert compute_payload_sha(python_rule_entries(tmp_path)) != after_rule


def _needs(cmd: str) -> None:
    if not shutil.which(cmd):
        pytest.fail(f"`{cmd}` toolchain not found on PATH; install it, do not skip")


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, **kw)


@pytest.fixture(scope="session")
def wheels(tmp_path_factory) -> dict:
    _needs("npm")
    _needs("go")
    _needs("uv")
    _needs("mvn")
    outdir = tmp_path_factory.mktemp("dist-wheels")
    env = {**os.environ, "TACKBOX_VERSION": "0.0.0", "TACKBOX_ENGINES_VERSION": "0.0.0"}
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--outdir", str(outdir), "--version", "0.0.0"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"build_wheels.py exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    payload = json.loads(result.stdout)
    return {"outdir": outdir, "fat": Path(payload["fat"]), "thin": Path(payload["thin"])}


@pytest.fixture(scope="session")
def hermetic_venv(tmp_path_factory, wheels) -> Path:
    """A fresh venv with ONLY the thin wheel installed.

    Installing thin alone is itself the F6 contract check: if thin still pinned
    tackbox-engines, uv would fail to resolve.
    """
    v = tmp_path_factory.mktemp("hermetic-venv")
    # uv venv, not python -m venv: off a uv-managed interpreter, libpython is
    # unresolvable via dyld @rpath on macOS.
    env = {k: val for k, val in os.environ.items() if k != "VIRTUAL_ENV"}
    subprocess.run(
        ["uv", "venv", str(v)],
        check=True, capture_output=True, text=True, env=env,
    )
    result = subprocess.run(
        ["uv", "pip", "install", "--python", str(_venv_bin(v, "python")), str(wheels["thin"])],
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"installing the thin wheel alone failed with exit {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return v


@pytest.fixture(scope="session")
def engines_payload(tmp_path_factory, wheels) -> Path:
    """The fat wheel's payload unpacked into a store-override directory, using
    the same unpack the runtime uses (exec bits restored)."""
    d = tmp_path_factory.mktemp("engines-payload") / "tackbox_engines"
    engines._unpack_tackbox_engines(wheels["fat"], d)
    return d


@pytest.fixture(scope="session")
def fixture_repo(tmp_path_factory) -> Path:
    parent = tmp_path_factory.mktemp("hermetic-fixture-parent")
    root = parent / "repo"
    subprocess.run(
        [sys.executable, str(MATERIALIZE_SCRIPT), str(root)],
        check=True, capture_output=True, text=True,
    )
    sources = {
        "unused.py": "import os\ndef public():\n    unused = 1\n    return 2\ndef _dead():\n    pass\n",
        "clean.py": "import json as _json\ndef public():\n    return 1\n",
        "pkga/unused.go": "package pkga\nfunc deadPrivate() {}\nfunc called(unused int) int { return 1 }\nfunc PublicUse() int { return called(3) }\n",
        "Unused.java": "import java.util.Map;\nclass Unused {\n    private int deadField;\n    private void deadMethod() {}\n    int publicUse() { int deadLocal = 1; return 2; }\n}\n",
        "unused.js": "const deadJs = 1;\nexport function publicJs() { return 2; }\n",
        "unused.ts": "type DeadType = number;\nexport const publicTs = 2;\n",
        "Unused.svelte": "<script>const deadSvelte = 1; const visible = 2;</script>\n<p>{visible}</p>\n",
    }
    for name, source in sources.items():
        (root / name).write_text(source, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    return root


def _venv_bin(venv: Path, name: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


def _tackbox(venv: Path) -> Path:
    return _venv_bin(venv, "tackbox")


def _hermetic_env(engines_payload: Path) -> dict:
    """os.environ with every PATH dir carrying an `ast-grep` removed: the venv
    is driven by entrypoint path, unactivated, exactly as launchers and CI
    drive it, and must be self-sufficient - an ast-grep from the dev venv or a
    global install would mask executable-resolution bugs."""
    def _carries_ast_grep(d: str) -> bool:
        return (Path(d) / "ast-grep").is_file() or (Path(d) / "ast-grep.exe").is_file()

    path = os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep)
        if p and not _carries_ast_grep(p)
    )
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    return {**env, "PATH": path, "TACKBOX_ENGINES_DIR": str(engines_payload)}


def test_thin_and_fat_wheels_built(wheels):
    assert wheels["fat"].is_file()
    assert wheels["thin"].is_file()
    assert wheels["fat"].name.endswith(".whl")
    assert wheels["thin"].name.endswith(".whl")


def test_build_restores_source_tree(wheels):
    """Leftover fat artifacts in py/tackbox would ride into every dev
    `uv run --directory py` rebuild - gigabytes of uv cache over a day."""
    pkg = REPO / "py" / "tackbox"
    leftover_bins = list((pkg / "bin").glob("*")) if (pkg / "bin").exists() else []
    assert leftover_bins == [], f"bin not restored: {leftover_bins}"
    assert not (pkg / "third_party").exists(), "third_party not restored"
    assert not (pkg / "rules").exists(), "materialized rules not restored"
    assert not (pkg / "engines.json").exists(), "engines.json not restored"


def test_thin_wheel_carries_javalint_jar(wheels):
    """F8a: the thin wheel ships the platform-independent javalint.jar and pins
    it in engines.json, so doctor checksums it and F8d can dispatch it. A
    dropped jar must fail the build here, not the consumer."""
    with zipfile.ZipFile(wheels["thin"]) as zf:
        names = set(zf.namelist())
        ej = json.loads(zf.read("tackbox/engines.json"))
    assert "tackbox/bin/javalint.jar" in names, sorted(
        n for n in names if n.startswith("tackbox/bin/")
    )
    entry = next((e for e in ej["engines"] if e.get("id") == "javalint"), None)
    assert entry is not None, "engines.json is missing the javalint entry"
    assert entry["path"] == "tackbox/bin/javalint.jar"
    assert entry.get("sha256"), "javalint entry must carry a sha256 for doctor"
    python_entries = python_rule_entries(REPO / 'py')
    by_id = {component['id']: component for component in ej['engines']}
    for component in python_entries:
        assert by_id[component['id']]['sha256'] == component['sha256']
        if component.get('path'):
            assert component['path'] in names


def test_thin_wheel_preserves_engine_dependency_licenses(wheels, tmp_path):
    dependency_file = tmp_path / "maven-runtime.txt"
    # Windows ships mvn as mvn.cmd, which CreateProcess resolves only by full path.
    mvn = shutil.which("mvn")
    assert mvn, "`mvn` toolchain not found on PATH; install it, do not skip"
    subprocess.run([
        mvn, "-q", "-B", "-f", str(REPO / "java" / "pom.xml"),
        "org.apache.maven.plugins:maven-dependency-plugin:3.7.0:list", "-DincludeScope=runtime", f"-DoutputFile={dependency_file}",
    ], check=True, capture_output=True, text=True)
    maven_dependencies = set()
    for line in dependency_file.read_text().splitlines():
        fields = line.strip().split(":")
        if len(fields) >= 5 and fields[-1].split()[0] in {"compile", "runtime"}:
            maven_dependencies.add((fields[0] + ":" + fields[1], fields[-2]))
    assert maven_dependencies
    assert not any("Saxon" in name or "xmlresolver" in name for name, _ in maven_dependencies)
    with zipfile.ZipFile(wheels["thin"]) as wheel:
        names = set(wheel.namelist())
        metadata = json.loads(wheel.read("tackbox/engines.json"))
        inventories = {}
        for engine in ("go", "javalint"):
            prefix = f"tackbox/third_party/licenses/{engine}/"
            inventory = json.loads(wheel.read(prefix + "inventory.json"))
            inventories[engine] = inventory["components"]
            assert wheel.read(prefix + "NOTICE.txt") == (REPO / "engines" / "licenses" / engine / "NOTICE.txt").read_bytes()
            for component in inventory["components"]:
                entry = next(e for e in metadata["engines"]
                             if e["id"] == f"{engine}-dependency/{component['id']}")
                assert entry["version"] == component["version"]
                assert entry["license"] == component["license"]
                assert entry["license_path"] == prefix + component["license_path"]
                source = REPO / "engines" / "licenses" / engine / component["license_path"]
                assert wheel.read(entry["license_path"]) == source.read_bytes()
                if component.get("notice_path"):
                    assert entry["notice_path"] == prefix + component["notice_path"]
                    assert wheel.read(entry["notice_path"]) == (source.parent / component["notice_path"]).read_bytes()
        assert {(c["id"], c["version"]) for c in inventories["javalint"]} == maven_dependencies
        linked_modules = set()
        build_versions = set()
        for command in ("erclint", "erclint-opengrep", "tackbox-jscpd"):
            path = next(n for n in names if n in {f"tackbox/bin/{command}", f"tackbox/bin/{command}.exe"})
            binary = tmp_path / Path(path).name
            binary.write_bytes(wheel.read(path))
            info = subprocess.run(["go", "version", "-m", str(binary)], check=True, capture_output=True, text=True).stdout
            build_versions.add(info.splitlines()[0].rsplit(': ', 1)[1].removeprefix('go'))
            for line in info.splitlines():
                fields = line.split()
                if fields and fields[0] == "dep":
                    linked_modules.add((fields[1], fields[2]))
        recorded_modules = {(c["id"], c["version"]) for c in inventories["go"]
                            if c.get("kind") != "runtime" and not c.get("vendored_source")}
        recorded_modules.update((c["id"], c["version"]) for c in inventories["go"]
                                if c.get("vendored_source") and c["id"] in dict(linked_modules))
        assert recorded_modules == linked_modules
        vendored = [c for c in inventories["go"] if c.get("vendored_source") and c["id"] not in dict(linked_modules)]
        assert {c["id"] for c in vendored} == {"golang.org/x/tools/gopls"}
        assert all((REPO / c["local_path"]).is_file() for c in vendored)
        assert {(c["id"], c["version"]) for c in inventories["go"] if c.get("kind") == "runtime"} == {
            ('go', version) for version in build_versions
        }
        with zipfile.ZipFile(io.BytesIO(wheel.read("tackbox/bin/javalint.jar"))) as jar:
            prefix = "tackbox/third_party/licenses/javalint/"
            for filename in (REPO / "engines" / "licenses" / "javalint").iterdir():
                assert jar.read("META-INF/third-party/" + filename.name) == wheel.read(prefix + filename.name)
            assert not any(n.startswith(("net/sf/saxon/", "org/xmlresolver/")) for n in jar.namelist())
            assert "META-INF/LICENSE.txt" not in jar.namelist()
            assert "META-INF/NOTICE.txt" not in jar.namelist()
            assert "META-INF/LICENSE" in jar.namelist()


def test_thin_wheel_does_not_depend_on_fat(wheels):
    """F6: thin must not carry a Requires-Dist on tackbox-engines - the engine
    payload is a machine-store fetch, not a pip dependency."""
    with zipfile.ZipFile(wheels["thin"]) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        metadata = zf.read(meta_name).decode("utf-8")
    requires = [ln for ln in metadata.splitlines() if ln.startswith("Requires-Dist:")]
    assert not any("tackbox-engines" in r or "tackbox_engines" in r for r in requires), (
        f"thin wheel still pins fat: {requires}"
    )
    # flake8 stays a real dependency (hosts the pyrules plugin); ast-grep-cli is
    # the pinned outline engine (D015), a thin runtime dep like flake8.
    for analyzer, version in (("flake8", "7.3.0"), ("pyflakes", "3.4.0")):
        assert any(analyzer in r and f"=={version}" in r for r in requires), requires
    assert any("ast-grep-cli" in r or "ast_grep_cli" in r for r in requires), (
        f"thin must pin the outline engine ast-grep-cli: {requires}"
    )
    assert any("==0.45.1" in r for r in requires if "ast" in r.lower()), (
        f"ast-grep-cli must be version-pinned: {requires}"
    )


def test_engines_json_carries_store_pins(engines_payload, wheels):
    """The thin wheel's engines.json must pin the fat wheel (name + this
    platform) and the unpacked tree, and the pin must match the real payload."""
    with zipfile.ZipFile(wheels["thin"]) as zf:
        ej = json.loads(zf.read("tackbox/engines.json"))
    assert ej["engines_version"] == "0.0.0"
    fw = ej["fat_wheel"]
    assert fw["wheel"] == wheels["fat"].name
    assert fw["platform"] == engines.detect_platform_key()
    assert "sha256" not in fw
    assert ej["store_sha256"] == sha256_tree(engines_payload)


def test_hermetic_doctor_exits_zero(hermetic_venv, fixture_repo, engines_payload):
    result = _run(
        [str(_tackbox(hermetic_venv)), "doctor"],
        cwd=fixture_repo, env=_hermetic_env(engines_payload),
    )
    assert result.returncode == 0, (
        f"doctor failed: {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "doctor: 8 checks, 0 failed" in result.stdout
    assert "ok platform:" in result.stdout
    assert "ok engines-store:" in result.stdout
    assert "ok payload-checksums:" in result.stdout
    assert "ok binaries-start:" in result.stdout
    assert "ok git-in-path:" in result.stdout
    assert "ok go-toolchain:" in result.stdout
    assert "ok java-toolchain:" in result.stdout
    # The outline engine ships as a thin runtime pip dep, so the pinned ast-grep
    # resolves into the same env as the wheel.
    assert "ok ast-grep:" in result.stdout


def test_hermetic_lint_finds_all_engine_violations(hermetic_venv, fixture_repo, engines_payload):
    result = _run(
        [str(_tackbox(hermetic_venv)), "lint", ".", "--no-cache"],
        cwd=fixture_repo, env=_hermetic_env(engines_payload),
    )
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "== erclint ==" in result.stdout
    assert "== erclint-opengrep ==" in result.stdout
    assert "== javalint ==" in result.stdout
    assert "== tackbox-eslint ==" in result.stdout
    assert "== tackbox-mdlint ==" in result.stdout
    # erclint ERC001 on pkga/violate.go
    assert "ERC001" in result.stdout
    assert "pkga" in result.stdout
    # javalint JV001 (swallowed catch) on Handler.java - the hermetic `java -jar`
    # path end to end, from the jar packed into the thin wheel.
    assert "JV001" in result.stdout
    assert "Handler.java" in result.stdout
    # same JV001, one directory deep: the repo-relative key must use "/" even
    # on a Windows runner (javasub/Deep.java, not javasub\Deep.java).
    assert "javasub/Deep.java" in result.stdout
    assert "javasub\\Deep.java" not in result.stdout
    # eslint no-swallow-catch on swallow.js
    assert "no-swallow-catch" in result.stdout
    # markdownlint declared-chars (MD-CHARS) on notes.md, under its chars=ascii carrier
    assert "MD-CHARS" in result.stdout or "declared-chars" in result.stdout
    for rule in ("python-unused-import", "python-unused-variable", "python-unused-private",
                 "ERC010", "ERC011", "JV011", "JV012", "JV013", "JV014",
                 "@typescript-eslint/no-unused-vars"):
        assert rule in result.stdout, result.stdout + result.stderr

    report_path = fixture_repo / "quality.json"
    quality = _run(
        [str(_tackbox(hermetic_venv)), "lint", ".", "--no-cache", "--codequality", str(report_path)],
        cwd=fixture_repo, env=_hermetic_env(engines_payload),
    )
    assert quality.returncode == 1, quality.stdout + quality.stderr
    issues = json.loads(report_path.read_text(encoding="utf-8"))
    locations = {(i['check_name'], i['location']['path'], i['location']['lines']['begin']) for i in issues}
    assert {
        ('python-unused-import', 'unused.py', 1),
        ('python-unused-variable', 'unused.py', 3),
        ('python-unused-private', 'unused.py', 5),
        ('unused', 'pkga/unused.go', 2), ('unusedparams', 'pkga/unused.go', 3),
        ('JV011', 'Unused.java', 5), ('JV012', 'Unused.java', 3),
        ('JV013', 'Unused.java', 4), ('JV014', 'Unused.java', 1),
        ('@typescript-eslint/no-unused-vars', 'unused.js', 1),
        ('@typescript-eslint/no-unused-vars', 'unused.ts', 1),
        ('@typescript-eslint/no-unused-vars', 'Unused.svelte', 1),
    } <= locations
    assert not any(i['location']['path'] == 'clean.py' for i in issues)


def test_doctor_fails_on_patched_vendored_transitive_dep(hermetic_venv, fixture_repo, engines_payload):
    """A byte flipped anywhere in the store payload must turn doctor red.

    Targets a transitive dep specifically: only the vendor-tree entry and the
    whole-tree store_sha256 cover those."""
    tackbox = _tackbox(hermetic_venv)
    env = _hermetic_env(engines_payload)
    engines_json = json.loads((_site_packages(hermetic_venv) / "tackbox" / "engines.json").read_text())
    top_level = {
        e["path"].split("node_modules/")[-1].split("/")[0]
        for e in engines_json["engines"]
        if e.get("kind") == "npm"
    }
    vendor = engines_payload / "vendor" / "node_modules"
    victim = next(
        p for p in sorted(vendor.rglob("*.js"))
        if p.is_file() and p.relative_to(vendor).parts[0] not in top_level
    )
    original = victim.read_bytes()
    try:
        victim.write_bytes(original + b"\n// locally patched\n")
        result = _run([str(tackbox), "doctor"], cwd=fixture_repo, env=env)
        assert result.returncode == 1, (
            f"patched vendored dep must fail doctor\nSTDOUT:\n{result.stdout}"
        )
        assert "fail engines-store:" in result.stdout
        assert "fail payload-checksums:" in result.stdout
    finally:
        victim.write_bytes(original)
    result = _run([str(tackbox), "doctor"], cwd=fixture_repo, env=env)
    assert result.returncode == 0, "doctor must recover after restore"


def test_hermetic_banner_carries_engines_sha_and_versions(hermetic_venv, fixture_repo, engines_payload):
    result = _run(
        [str(_tackbox(hermetic_venv)), "lint", ".", "--no-cache"],
        cwd=fixture_repo, env=_hermetic_env(engines_payload),
    )
    banner = None
    for line in result.stderr.splitlines():
        if line.startswith("tackbox "):
            banner = line
            break
    assert banner is not None, f"no banner on stderr:\n{result.stderr}"
    assert "engines=sha256:" in banner
    assert "opengrep=1.25.0" in banner
    assert "node=22.18.0" in banner
    assert "eslint=" in banner
    assert "markdownlint=" in banner


def _site_packages(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Lib" / "site-packages"
    return next((venv / "lib").glob("python*/site-packages"))

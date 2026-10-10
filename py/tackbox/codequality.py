"""CodeClimate-format report for `lint --codequality <path>`.

The GitLab MR widget (artifacts:reports:codequality) consumes a JSON array of
issue objects. description is `rule: message` when the finding carries a
message (the widget often shows only description, so it is self-contained)
and the bare rule id otherwise; path/line default to the pseudo-location
UNKNOWN:1 when the engine could not locate the finding.
Engine failures use critical severity and their scope at line 1 (repo root
when unknown); their engine id participates in the fingerprint.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from . import proc
from .engines import (
    EngineOutput,
    EngineResult,
    Finding,
    erclint_base_import_path,
    iter_json_objects,
    located_findings,
)
from .source_set import module_relative

# Repo-root-relative pseudo-path for a location-unknown finding: "" is invalid
# in the CodeClimate location.path, so unlocated findings sort under UNKNOWN:1.
_UNKNOWN_PATH = "UNKNOWN"

_DUP_PREFIX = "DUP"
_ENGINE_ERROR = "tackbox-engine-error"

# A per-finding fingerprint override: given a finding, return its fingerprint or
# None to fall back to the default sha256(rule:path:line). tackbox-approvals
# supplies the serialized entry address here so a marker's identity - not its
# line - keys the MR widget; engine collisions get an occurrence suffix.
FingerprintOf = Callable[[Finding], "str | None"]


def _issue(f: Finding, fingerprint_of: FingerprintOf | None = None, occurrence: int = 0) -> dict:
    path = f.file if f.file is not None else _UNKNOWN_PATH
    line = f.line if f.line is not None else 1
    category = "Duplication" if f.rule.startswith(_DUP_PREFIX) else "Bug Risk"
    # Message stays out of the fingerprint: rewording a diagnostic must not
    # re-open resolved issues in the MR widget.
    override = fingerprint_of(f) if fingerprint_of is not None else None
    identity = f"{f.rule}:{path}:{line}"
    if f.rule == _ENGINE_ERROR:
        identity += f":{f.message.split(':', 1)[0]}"
    if occurrence:
        identity += f":{occurrence}"
    fingerprint = override if override is not None else hashlib.sha256(identity.encode()).hexdigest()
    description = f"{f.rule}: {' '.join(f.message.split())}" if f.message else f.rule
    return {
        "type": "issue",
        "check_name": f.rule,
        "description": description,
        "categories": [category],
        "location": {"path": path, "lines": {"begin": line}},
        "fingerprint": fingerprint,
        "severity": "critical" if f.rule == _ENGINE_ERROR else "major",
    }


def build_report(findings: list[Finding], fingerprint_of: FingerprintOf | None = None) -> list[dict]:
    """Issue objects sorted by (path, line, rule) for a stable artifact."""
    ordered = sorted(findings, key=lambda f: (f.file or _UNKNOWN_PATH, f.line or 1, f.rule))
    occurrences: dict[tuple, int] = {}
    issues = []
    for finding in ordered:
        key = (finding.rule, finding.file or _UNKNOWN_PATH, finding.line or 1)
        if finding.rule == _ENGINE_ERROR:
            key += (finding.message.split(":", 1)[0],)
        occurrence = occurrences.get(key, 0)
        issues.append(_issue(finding, fingerprint_of, occurrence))
        occurrences[key] = occurrence + 1
    return issues


def _package_paths(output: EngineOutput, root: Path) -> dict[str, str]:
    completed = proc.run(
        ["go", "list", "-e", "-json", *[f"./{module_relative(output.scope, target)}" for target in output.targets]],
        cwd=root / output.scope, capture_output=True, check=True,
    )
    return {package["ImportPath"]: Path(package["Dir"]).relative_to(root).as_posix()
            for package in iter_json_objects(completed.stdout)}


def engine_findings(result: EngineResult, root: Path) -> list[Finding]:
    """Located diagnostics plus one critical issue per failed engine scope."""
    findings = located_findings(result.engine_id, result.stdout, root)
    outputs = result.outputs or (EngineOutput(".", (), result.exit_code, result.stdout, result.stderr),)
    errors: list[Finding] = []
    for output in outputs:
        reasons: dict[str, list[str]] = {}
        if result.engine_id in ("erclint", "javalint"):
            packages = {}
            for document in iter_json_objects(output.stdout):
                for package, analyzers in document.items():
                    for analyzer, payload in analyzers.items():
                        if isinstance(payload, dict) and "error" in payload:
                            key = erclint_base_import_path(package) if result.engine_id == "erclint" else package
                            packages.setdefault(key, []).append(f"{analyzer}: {payload['error']}")
            paths = _package_paths(output, root) if packages and result.engine_id == "erclint" else {}
            for package, details in packages.items():
                path = paths.get(package, output.scope) if result.engine_id == "erclint" else package
                reasons.setdefault(path, []).extend(details)
        if output.exit_code and not reasons and not located_findings(result.engine_id, output.stdout, root):
            reasons[output.scope] = [output.stderr or output.stdout or f"exit {output.exit_code} without findings"]
        errors.extend(Finding(_ENGINE_ERROR, path or ".", 1,
                              f"{result.engine_id}: {'; '.join(dict.fromkeys(details))}")
                      for path, details in reasons.items())
    return findings + errors


def write_report(path: Path, findings: list[Finding],
                 fingerprint_of: FingerprintOf | None = None) -> None:
    """Write the report to `path`; an unwritable path raises OSError loudly."""
    text = json.dumps(build_report(findings, fingerprint_of), indent=2) + "\n"
    path.write_text(text, encoding="utf-8")

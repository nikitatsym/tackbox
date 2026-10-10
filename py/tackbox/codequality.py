"""CodeClimate-format report for `lint --codequality <path>`.

The GitLab MR widget (artifacts:reports:codequality) consumes a JSON array of
issue objects. description is `rule: message` when the finding carries a
message (the widget often shows only description, so it is self-contained)
and the bare rule id otherwise; path/line default to the pseudo-location
UNKNOWN:1 when the engine could not locate the finding.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from .engines import Finding

# Repo-root-relative pseudo-path for a location-unknown finding: "" is invalid
# in the CodeClimate location.path, so unlocated findings sort under UNKNOWN:1.
_UNKNOWN_PATH = "UNKNOWN"

_DUP_PREFIX = "DUP"

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
        "severity": "major",
    }


def build_report(findings: list[Finding], fingerprint_of: FingerprintOf | None = None) -> list[dict]:
    """Issue objects sorted by (path, line, rule) for a stable artifact."""
    ordered = sorted(findings, key=lambda f: (f.file or _UNKNOWN_PATH, f.line or 1, f.rule))
    occurrences: dict[tuple[str, str, int], int] = {}
    issues = []
    for finding in ordered:
        key = (finding.rule, finding.file or _UNKNOWN_PATH, finding.line or 1)
        occurrence = occurrences.get(key, 0)
        issues.append(_issue(finding, fingerprint_of, occurrence))
        occurrences[key] = occurrence + 1
    return issues


def write_report(path: Path, findings: list[Finding],
                 fingerprint_of: FingerprintOf | None = None) -> None:
    """Write the report to `path`; an unwritable path raises OSError loudly."""
    text = json.dumps(build_report(findings, fingerprint_of), indent=2) + "\n"
    path.write_text(text, encoding="utf-8")

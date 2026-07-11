"""CodeClimate-format report for `lint --codequality <path>`.

The GitLab MR widget (artifacts:reports:codequality) consumes a JSON array of
issue objects. Findings carry only {rule, file, line}, so description == the
rule id (no message to relay); path/line default to the pseudo-location
UNKNOWN:1 when the engine could not locate the finding.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .engines import Finding

# Repo-root-relative pseudo-path for a location-unknown finding: "" is invalid
# in the CodeClimate location.path, so unlocated findings sort under UNKNOWN:1.
_UNKNOWN_PATH = "UNKNOWN"

_DUP_PREFIX = "DUP"


def _issue(f: Finding) -> dict:
    path = f.file if f.file is not None else _UNKNOWN_PATH
    line = f.line if f.line is not None else 1
    category = "Duplication" if f.rule.startswith(_DUP_PREFIX) else "Bug Risk"
    fingerprint = hashlib.sha256(f"{f.rule}:{path}:{line}".encode()).hexdigest()
    return {
        "type": "issue",
        "check_name": f.rule,
        "description": f.rule,
        "categories": [category],
        "location": {"path": path, "lines": {"begin": line}},
        "fingerprint": fingerprint,
        "severity": "major",
    }


def build_report(findings: list[Finding]) -> list[dict]:
    """Issue objects sorted by (path, line, rule) for a stable artifact."""
    issues = [_issue(f) for f in findings]
    issues.sort(
        key=lambda i: (i["location"]["path"], i["location"]["lines"]["begin"], i["check_name"])
    )
    return issues


def write_report(path: Path, findings: list[Finding]) -> None:
    """Write the report to `path`; an unwritable path raises OSError loudly."""
    text = json.dumps(build_report(findings), indent=2) + "\n"
    path.write_text(text, encoding="utf-8")

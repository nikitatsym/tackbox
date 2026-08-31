"""The versioned host-neutral hook protocol.

Hosts describe mutations as absolute event paths and precise target operations. The
policy core returns a closed semantic outcome; only this module maps it to the
legacy host wire decisions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

VERSION = 1

PRE = "pre"
POST = "post"

EDIT = "edit"
WRITE = "write"
DELETE = "delete"
MOVE = "move"
_OPERATIONS = frozenset({EDIT, WRITE, DELETE, MOVE})
_PROTOCOL_TOOLS = frozenset({"edit", "apply_patch", "write", "bash", "eval"})
_OPAQUE = "opaque"
_NO_OP = "no-op"
_FAILED = "failed"
_TARGETLESS = frozenset({_OPAQUE, _NO_OP, _FAILED})
_OPAQUE_TOOLS = frozenset({"bash", "eval"})


class OutcomeKind(str, Enum):
    """Every semantic result the policy core may produce."""

    INACTIVE = "inactive"
    ALLOW = "allow"
    APPROVAL_REQUIRED = "approval-required"
    VIOLATION = "violation"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Outcome:
    """A semantic outcome with host-facing text where an action is needed."""

    kind: OutcomeKind
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OutcomeKind):
            raise TypeError("outcome.kind must be an OutcomeKind")
        if not isinstance(self.reason, str):
            raise TypeError("outcome.reason must be a string")
        clean = self.kind in {OutcomeKind.INACTIVE, OutcomeKind.ALLOW}
        if clean and self.reason:
            raise ValueError(f"{self.kind.value} outcome cannot carry a reason")
        if not clean and not self.reason:
            raise ValueError(f"{self.kind.value} outcome requires a reason")


@dataclass(frozen=True)
class Target:
    """One file mutation with its landed-presence contract.

    A move is represented twice when policy needs both paths: its source has
    ``expected_present=False`` and its destination has ``expected_present=True``.
    """

    path: Path
    operation: str = EDIT
    expected_present: bool = True
    content: str | None = None
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    ambiguous: bool = False
    move_id: str | None = None


@dataclass(frozen=True)
class Event:
    """One tool call in one phase.

    ``succeeded`` is meaningful only after the tool result: an expected-present
    target may be absent after a failed operation without becoming unverified.
    ``targetless`` distinguishes an opaque channel, a verified no-op, and an
    aggregate failure with no record-level landed target.
    """

    phase: str
    cwd: str
    tool: str
    targets: tuple[Target, ...] = ()
    unknown: str | None = None
    succeeded: bool = True
    targetless: str | None = None

@dataclass(frozen=True)
class WireDecision:
    """The small compatibility vocabulary rendered for protocol hosts."""

    decision: str
    reason: str = ""


class HookProtocolError(ValueError):
    """A request this tackbox does not speak (bad version, phase, or shape)."""


def parse_request(payload: object) -> Event:
    """Validate one strict protocol request and build its event.

    A malformed host request is not weakened into a guessed mutation. Hosts that
    cannot produce this shape must surface an unverified result themselves.
    """
    if not isinstance(payload, dict):
        raise HookProtocolError("hook request must be a JSON object")
    protocol = payload.get("protocol")
    if type(protocol) is not int or protocol != VERSION:
        raise HookProtocolError(
            f"unsupported protocol {protocol!r}; this tackbox speaks {VERSION}"
        )
    phase = payload.get("phase")
    if phase not in (PRE, POST):
        raise HookProtocolError(f"phase must be {PRE!r} or {POST!r}, got {phase!r}")
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise HookProtocolError("cwd must be a non-empty string")
    if not Path(cwd).is_absolute():
        raise HookProtocolError(f"cwd must be absolute, got {cwd!r}")
    tool = payload.get("tool")
    if not isinstance(tool, str) or tool not in _PROTOCOL_TOOLS:
        raise HookProtocolError(
            f"tool must be one of {sorted(_PROTOCOL_TOOLS)!r}, got {tool!r}"
        )
    if "targets" not in payload:
        raise HookProtocolError("targets must be a list")
    raw_targets = payload["targets"]
    if not isinstance(raw_targets, list):
        raise HookProtocolError("targets must be a list")
    unknown = payload.get("unknown")
    if unknown is not None and (not isinstance(unknown, str) or not unknown):
        raise HookProtocolError("unknown must be a non-empty string or absent")
    if unknown is not None and raw_targets:
        raise HookProtocolError("unknown cannot accompany concrete targets")
    targetless = payload.get("targetless")
    if targetless is not None and targetless not in _TARGETLESS:
        raise HookProtocolError(
            f"targetless must be one of {sorted(_TARGETLESS)!r} or absent"
        )
    if unknown is not None and targetless is not None:
        raise HookProtocolError("unknown cannot accompany targetless")
    if phase == PRE:
        if "succeeded" in payload:
            raise HookProtocolError("pre requests cannot carry succeeded")
        succeeded = True
    else:
        succeeded = payload.get("succeeded")
        if type(succeeded) is not bool:
            raise HookProtocolError("post.succeeded must be a boolean")
    targets = tuple(_target(raw) for raw in raw_targets)
    _validate_event(tool, targets, targetless)
    return Event(
        phase=phase,
        cwd=cwd,
        tool=tool,
        targets=targets,
        unknown=unknown,
        succeeded=succeeded,
        targetless=targetless,
    )


def _validate_event(
    tool: str,
    targets: tuple[Target, ...],
    targetless: str | None,
) -> None:
    if tool in _OPAQUE_TOOLS:
        if targets or targetless != _OPAQUE:
            raise HookProtocolError("bash and eval requests must be targetless opaque")
        return
    if tool == WRITE and any(target.operation != WRITE for target in targets):
        raise HookProtocolError("write requests can carry only write targets")
    _validate_move_pairs(targets)


def _validate_move_pairs(targets: tuple[Target, ...]) -> None:
    pairs: dict[str, list[Target]] = {}
    for target in targets:
        if target.operation == MOVE:
            assert target.move_id is not None
            pairs.setdefault(target.move_id, []).append(target)
    for move_id, pair in pairs.items():
        sources = [target for target in pair if not target.expected_present]
        destinations = [target for target in pair if target.expected_present]
        if len(pair) != 2 or len(sources) != 1 or len(destinations) != 1:
            raise HookProtocolError(
                f"move pair {move_id!r} must name one source and one destination"
            )
        if sources[0].path == destinations[0].path:
            raise HookProtocolError(f"move pair {move_id!r} cannot use one path twice")


def wire_decision(outcome: Outcome, phase: str) -> WireDecision:
    """Map a semantic outcome to the established protocol decision wire."""
    if phase not in (PRE, POST):
        raise ValueError(f"unsupported hook phase {phase!r}")
    if outcome.kind in {OutcomeKind.INACTIVE, OutcomeKind.ALLOW}:
        return WireDecision("allow")
    if outcome.kind is OutcomeKind.APPROVAL_REQUIRED:
        if phase == PRE:
            return WireDecision("ask", outcome.reason)
        return WireDecision("block", outcome.reason)
    if outcome.kind is OutcomeKind.VIOLATION:
        return WireDecision("block", outcome.reason)
    if outcome.kind is OutcomeKind.UNVERIFIED:
        return WireDecision("block" if phase == PRE else "warn", outcome.reason)
    raise AssertionError(f"unhandled outcome kind {outcome.kind!r}")


def render_decision(outcome: Outcome, phase: str) -> str:
    """Render the one-line JSON response for a semantic outcome."""
    decision = wire_decision(outcome, phase)
    return json.dumps(
        {"protocol": VERSION, "decision": decision.decision, "reason": decision.reason}
    )


def _target(raw: object) -> Target:
    if not isinstance(raw, dict):
        raise HookProtocolError("every target must be an object")
    path = raw.get("path")
    if not isinstance(path, str) or not path:
        raise HookProtocolError("target.path must be a non-empty string")
    target_path = Path(path)
    if not target_path.is_absolute():
        raise HookProtocolError(f"target.path must be absolute, got {path!r}")
    operation = raw.get("op")
    if not isinstance(operation, str) or operation not in _OPERATIONS:
        raise HookProtocolError(
            f"target.op must be one of {sorted(_OPERATIONS)!r}, got {operation!r}"
        )
    expected_present = raw.get("expectedPresent")
    if type(expected_present) is not bool:
        raise HookProtocolError("target.expectedPresent must be a boolean")
    ambiguous = raw.get("ambiguous", False)
    if type(ambiguous) is not bool:
        raise HookProtocolError("target.ambiguous must be a boolean")
    move_id = raw.get("moveId")
    if operation == MOVE:
        if not isinstance(move_id, str) or not move_id:
            raise HookProtocolError("a move target requires a non-empty moveId")
    elif move_id is not None:
        raise HookProtocolError("only move targets can carry moveId")
    has_content = "content" in raw
    has_fragments = "added" in raw or "removed" in raw
    if has_content and has_fragments:
        raise HookProtocolError("target.content is mutually exclusive with added or removed")
    if has_content:
        content = raw["content"]
        if not isinstance(content, str):
            raise HookProtocolError(f"target.content of {path} must be a string")
        added: tuple[str, ...] = ()
        removed: tuple[str, ...] = ()
    else:
        content = None
        added = _fragments(raw.get("added"), path, "added")
        removed = _fragments(raw.get("removed"), path, "removed")
    if operation in {EDIT, WRITE} and not expected_present:
        raise HookProtocolError(f"target.op {operation!r} requires expectedPresent=true")
    if operation == DELETE and expected_present:
        raise HookProtocolError("target.op 'delete' requires expectedPresent=false")
    if operation == DELETE and (has_content or "added" in raw):
        raise HookProtocolError("a delete target cannot carry landed content")
    if (
        operation not in {DELETE, MOVE}
        and not has_content
        and not has_fragments
        and not ambiguous
    ):
        raise HookProtocolError("target must carry content, fragments, or ambiguous=true")
    if has_content and ambiguous:
        raise HookProtocolError("target.content cannot accompany ambiguous=true")
    return Target(
        path=target_path,
        operation=operation,
        expected_present=expected_present,
        content=content,
        added=added,
        removed=removed,
        ambiguous=ambiguous,
        move_id=move_id,
    )


def _fragments(raw: object, path: str, key: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(not isinstance(s, str) for s in raw):
        raise HookProtocolError(f"target.{key} of {path} must be a list of strings")
    return tuple(raw)

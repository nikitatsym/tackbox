# Agent hook

The contract of `tackbox hook` (Claude Code) and `tackbox hook-protocol`
(every other host). Wiring per host is in the
[README](../README.md#agent-hook).

## Session and gates

The rules wire into a coding agent's edit loop through one shared core:
approval gates, diff-scoped lint, and session approvals debt have the same
semantics whichever host drives them. Session means each target worktree's
difference from HEAD, including staged changes and untracked files; it is not
a snapshot taken when the agent starts. Without a commit, the whole tree is
added. Existing committed debt is left to `dev.py check` and CI; dirty debt
already present when an agent starts still belongs to the hook session.

An import can be temporarily unused between edits. The post hook reports it
on the added line without undoing the edit; adding its use clears the finding.
Add the import and its use in one edit when possible. Do not delete a needed
import just to silence feedback during a multi-step change.

- **Post-edit** re-lints touched files (Go: their package). A finding on
  added lines or session approvals debt in a touched file becomes a tool
  error carrying the findings. The mutation has already landed; this is
  not a block or rollback. Debt includes a new uncovered marker, a new
  orphaned approval, and an approval whose marker was deleted.
  A verified violation is always a tool error. A post event
  that cannot be verified reports three facts: the mutation may already have
  landed, why verification did not complete, and that the mutation must not be
  repeated before `dev.py check`. OMP appends that warning to the model-facing
  tool result without changing a successful tool state. Claude Code writes it
  to user-visible PostToolUse stderr with exit 1, so it is not model-visible.
- **Pre-edit** asks for approval before a new `.tackbox/approvals` line or a
  new `.tackbox/reporters` line lands, before a positive exclusion line is
  added to any `.gitattributes`, before editing an attribute-excluded file, and
  before deleting or moving the root `dev.py`; removing a gate line is free.
  A known target whose content is ambiguous asks when it reaches a bypass
  surface. An unclassifiable file mutation or a failed policy dependency blocks
  before it can run; it is never weakened into an approval prompt.
  Session approvals debt also blocks an edit if any target in that repository
  is outside its repair set: whole files of the debt's markers or unresolvable
  files, files referenced by orphaned entries, and `.tackbox/approvals`.
  A multi-file edit with one unrelated target in an indebted repository is
  refused as a whole. Repairing debt in repository A while editing clean
  repository B is permitted; A's debt does not restrict B's targets. The
  existing approval gates still apply to every permitted repair.

Each explicit file target discovers its own Git worktree from its resolved path,
using the nearest existing parent for new files and deleted paths. The session
`cwd` only resolves relative host paths; it does not select repository policy.
Cross-repository edits and moves check every affected worktree and produce one
indivisible pre decision. All approval reasons and blockers remain visible, with
repository paths identifying multi-worktree results. A blocked or unverified
target prevents the entire pre mutation; post results check only targets that
the host reports as landed and never claim rollback.

A target is inactive only when discovery emits C-locale stderr containing
`not a git repository`, or its discovered worktree has no root `dev.py`.
A missing git executable, unreadable parent, corrupt Git config, or another
discovery failure is unverified, not a no-op. Unclassifiable explicit file
mutations fail closed even when the session starts outside Git.

## Exit codes

- **hook** - `0` a no-op, a clean event, or a JSON pre decision (an
  approval prompt or a refusal before an unrelated edit); `1` a
  non-blocking infra error (unreadable stdin, a git failure); `2` a
  PostToolUse finding on edited lines, a non-compiling Go package, or
  session approvals debt in a touched file. A post error reports a
  mutation that already landed; it does not block or undo the write.
- **hook-protocol** - `0` whenever a decision was reached, whatever the
  decision says (it rides the JSON on stdout, never the exit code); `1`
  plus one stderr line when none was: unreadable stdin, or a request
  whose protocol version this tackbox does not speak.

## Codex

Codex exposes its canonical file mutation as `apply_patch`. Wire the npm
adapter once, globally, in `~/.codex/hooks.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "apply_patch",
       "hooks": [{"type": "command",
                  "command": "tackbox-codex-hook"}]}
    ],
    "PostToolUse": [
      {"matcher": "apply_patch",
       "hooks": [{"type": "command",
                  "command": "tackbox-codex-hook",
                  "timeout": 120}]}
    ]
  }
}
```

The adapter converts Codex's patch payload to `tackbox hook-protocol` and pins
the Python wheel to the resolved npm package version.
Before a patch, any non-allow decision blocks. After a patch, verified findings
replace the tool result; an unverified decision adds model-visible context that
the mutation may already have landed and must not be repeated.

## Oh My Pi

The npm package declares an extension (`package.json#omp.extensions`) that
runs `uvx tackbox@<npm package version> hook-protocol`. The extension
subscribes to the public `tool_call` / `tool_result` events.

- a pre-edit ask becomes a confirmation dialog. In a headless or subagent
  session, where nobody can answer, it blocks with the reason instead of
  approving itself; a denied ask blocks before the tool runs.
- a verified post violation becomes a tool error carrying the findings, which
  keeps the agent in-loop on them.
- an unverified pre event blocks. The child has a 20-second deadline inside
  OMP's 30-second handler budget. An unverified post event carries the shared
  three-fact warning, omits an `isError` override so OMP preserves the host
  state, and tells the model not to repeat the mutation before `dev.py check`.
- an opaque write channel (`xd://` tool devices, archive members, SQLite rows),
  every `bash` call, and every `eval` call name no file, so they run
  neither approvals checks nor targeted lint.
- MCP tool names are not enumerated by this extension. Their file mutations are
  an explicit residual outside its pre gate and targeted post checks;
  review their diff and run `dev.py check`.
- the post adapter consumes each result-detail record independently. It falls
  back to a snapshot only for that record when the record is pruned; failed
  records do not widen the scope of successful landed records. OMP 18.x does
  not identify a landed subset for a single aggregate error without per-file
  details, so Tackbox preserves the host error and cannot safely perform
  targeted lint for that residual. It does not run a whole-tree approvals check.

## Other hosts

`tackbox hook-protocol` is the host-neutral wire - one JSON event on stdin,
one JSON decision on stdout:

```json
{"protocol": 1, "phase": "pre", "cwd": "/repo", "tool": "edit",
 "targets": [{"path": "/repo/app/svc.py", "op": "edit",
              "expectedPresent": true,
              "added": ["x = 2"], "removed": ["x = 1"]}],
 "unknown": null}
```

```json
{"protocol": 1, "decision": "ask",
 "reason": "approve suppression marker: app/svc.py: no-report: covered upstream"}
```

- `cwd` is the host session's non-empty absolute working directory, used by
  adapters to resolve relative paths. Each target selects its own repository.
- `phase` is `pre` (before the tool runs, still refusable) or `post` (after it
  landed). Pre requests omit `succeeded`; post requests require the boolean
  `succeeded`, so a failed tool is not misreported as a missing landed file.
- `tool` is one of `edit`, `apply_patch`, `write`, `bash`, or `eval`. `bash` and
  `eval` are target-free channels and run no approvals or targeted lint.
- a **target** is one file mutation with an absolute `path`, `op`, and
  `expectedPresent`. `edit` and `write` expect the path to exist, `delete`
  expects it absent, and a move reports an absent source plus a present
  destination. `content` is a full replacement; otherwise `added` and
  `removed` are text fragments. `content` and fragments are mutually exclusive.
  `ambiguous: true` means a known target needs whole-file treatment.
- **zero targets** is the opaque channel: no approvals or targeted lint runs.
  `unknown` is a non-empty reason only when no concrete target can be named;
  it blocks pre and warns post.
- the wire decisions are `allow`, `ask`, `block`, and `warn`. The semantic
  outcomes are inactive, allow, approval-required, violation, and unverified:
  unverified maps to `block` pre and `warn` post. Hosts must make a post warning
  visible without turning a successful mutation into a repeatable tool error.

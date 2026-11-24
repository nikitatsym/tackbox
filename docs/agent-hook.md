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

An import that stays unused until a later edit is reported on its added line.
Add the import and its use in one edit when possible; do not delete a needed
import to silence the finding.

- **Post-edit** re-lints touched files (Go: their package). A finding on
  added lines or session approvals debt in a touched file becomes a tool
  error carrying the findings. The mutation has already landed; this is
  not a block or rollback. Debt includes a new uncovered marker, a new
  orphaned approval, and an approval whose marker was deleted.
  A post event that cannot be verified reports three facts: the mutation may
  already have landed, why verification did not complete, and that the
  mutation must not be repeated before `dev.py check`. OMP appends that
  warning to the model-facing tool result without changing a successful tool
  state. Claude Code writes it to user-visible PostToolUse stderr with exit 1,
  so it is not model-visible.
- **Pre-edit** asks for approval before a new `.tackbox/approvals` line or a
  new `.tackbox/reporters` line lands, before a positive exclusion line is
  added to any `.gitattributes`, before editing an attribute-excluded file, and
  before deleting or moving the root `dev.py`. Removing a gate line is free, as
  are `=false`, `-attr`, and other non-exclusion `.gitattributes` lines; one
  edit that adds several gated lines draws one all-or-nothing ask.
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
the host reports as landed.

A target outside any Git worktree, or in a worktree without a root `dev.py`,
is inactive: the hook allows it. A discovery failure (git missing, an
unreadable parent, a corrupt Git config) makes the target unverified instead,
and an explicit file mutation the hook cannot classify fails closed even when
the session starts outside Git.

## Exit codes

- **hook** - `0` a no-op, a clean event, or a JSON pre decision (an
  approval prompt or a refusal before an unrelated edit); `1` a
  non-blocking infra error (unreadable stdin, a git failure); `2` a
  PostToolUse finding on edited lines, a non-compiling Go package, or
  session approvals debt in a touched file.
- **hook-protocol** - `0` whenever a decision was written, whatever the
  decision says (it rides the JSON on stdout, never the exit code); `1` and
  no decision for unreadable stdin or a request the wire below rejects.

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

The adapter converts Codex's patch payload to `tackbox hook-protocol`, run
through the pinned wheel ([README](../README.md#agent-hook)).
Before a patch, any non-allow decision blocks. After a patch, verified findings
replace the tool result; an unverified decision adds model-visible context that
the mutation may already have landed and must not be repeated.

## Oh My Pi

The extension runs `tackbox hook-protocol` for each edit, through the pinned
wheel ([README](../README.md#agent-hook)).

- A pre-edit ask becomes a confirmation dialog. In a headless or subagent
  session, where nobody can answer, it blocks with the reason instead of
  approving itself; a denied ask blocks before the tool runs.
- A verified post violation becomes a tool error carrying the findings, which
  keeps the agent in-loop on them.
- An unverified pre event blocks, including a check that does not answer
  within 20 seconds. An unverified post event keeps the tool's own result and
  adds the warning described under Post-edit.
- An opaque write channel (`xd://` tool devices, archive members, SQLite rows),
  every `bash` call, and every `eval` call name no file, so they run
  neither approvals checks nor targeted lint.
- MCP tool names are not enumerated by this extension. Their file mutations are
  an explicit residual outside its pre gate and targeted post checks;
  review their diff and run `dev.py check`.
- When OMP cannot say which files a failed edit landed, the hook keeps the
  host's error and skips targeted lint; run `dev.py check`.

## Other hosts

`tackbox hook-protocol` reads one JSON request on stdin and writes one JSON
decision on stdout. A pre request for an edit that adds an approval line, and
its response:

```json
{"phase": "pre", "cwd": "/repo", "tool": "edit",
 "targets": [{"path": "/repo/.tackbox/approvals", "op": "edit",
              "expectedPresent": true,
              "added": ["app/svc.py#run: no-report: the caller retries the whole batch"]}]}
```

```json
{"decision": "ask", "reason": "approve suppression marker: app/svc.py#run: no-report: the caller retries the whole batch"}
```

Request fields; a request that breaks any of these rules gets exit `1` and no
decision:

- `phase` is `pre` (before the tool runs, still refusable) or `post` (after it
  ran). A post request requires the boolean `succeeded`, so a failed tool is
  not misreported as a missing landed file; a pre request must not carry it.
- `cwd` is the host session's absolute working directory, used only to
  resolve relative host paths. Each target selects its own repository.
- `tool` is one of `edit`, `apply_patch`, `write`, `bash`, or `eval`.
- `targets` is a required list; each target is one file mutation:
  - an absolute `path`, an `op` (`edit`, `write`, `delete`, or `move`), and a
    boolean `expectedPresent`: `true` for `edit` and `write`, `false` for
    `delete`;
  - a move is two targets with the same non-empty `moveId`, the source with
    `expectedPresent: false` and the destination with `true`; only move
    targets carry `moveId`;
  - `content` (the full new text) or `added` / `removed` (lists of text
    fragments), never both. `ambiguous: true` marks a known target that needs
    whole-file treatment; it may come with fragments, never with `content`.
    Every target except a delete or a move carries `content`, fragments, or
    `ambiguous: true`, and a delete carries no `content` or `added`;
  - a `write` request carries only `write` targets.
- A request with no targets and no `unknown` runs no checks and is allowed.
  `targetless` says why: `opaque` (a channel that names no file; required
  for `bash` and `eval`, which never carry targets), `no-op` (the tool changed
  nothing), or `failed` (a failed tool with no landed target).
- `unknown` is a non-empty reason for a mutation whose files the host cannot
  name. It excludes targets and `targetless`, and it blocks pre and warns post.

The response is `{"decision": ..., "reason": ...}`, with an
empty reason for `allow`. The decisions map the semantic outcomes: inactive and
allow give `allow`; approval-required gives `ask` pre and `block` post; a
violation gives `block`; unverified gives `block` pre and `warn` post. Hosts
must make a post warning visible without turning a successful mutation into a
repeatable tool error.

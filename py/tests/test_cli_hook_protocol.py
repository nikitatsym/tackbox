"""`tackbox hook-protocol`: the versioned host-neutral hook protocol (v1).

Drives `python -m tackbox.cli hook-protocol` with one protocol event on stdin
and pins the decision object on stdout. Every case runs the subprocess from
TACKBOX_ROOT (a git repo with no dev.py, so the guard fails there) and points
the event's `cwd` at the fixture instead: the protocol derives the repo from the
event, never from the process.

The Claude Code host has its own suite (test_cli_hook.py). One case here pins
the invariant that binds them - the same change draws the same text through both
hosts, because both render one shared decision.
"""

from __future__ import annotations

import json
import subprocess
import sys
import pytest
from pathlib import Path

from conftest import init_repo, tackbox_env

from tackbox import cli, hookproto

TACKBOX_ROOT = Path(__file__).resolve().parents[2]

SVELTE_SWALLOW = "<script>\ntry { f() } catch (e) {}\n</script>\n"
SVELTE_CLEAN = "<script>\nexport let name = 'x'\n</script>\n"

MANIFEST_ENTRY = "app/svc.py#Handler.process: no-report: legacy path, covered upstream"
CANON_SINGLE = f"approve suppression marker: {MANIFEST_ENTRY}"


def _repo(root: Path) -> None:
    (root / "dev.py").write_text("# stub dev.py so the hook guard fires\n")
    init_repo(root, commit=True)


def _run(payload: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "hook-protocol"],
        input=payload,
        cwd=TACKBOX_ROOT,
        env=tackbox_env(),
        capture_output=True,
        text=True,
    )


def _event(
    phase: str,
    root: Path,
    tool: str,
    targets: list[dict],
    unknown=None,
    targetless=None,
    succeeded: bool = True,
) -> dict:
    if not targets and unknown is None and targetless is None and tool in {"bash", "eval"}:
        targetless = "opaque"
    event = {
        "protocol": 1,
        "phase": phase,
        "cwd": str(root),
        "tool": tool,
        "targets": targets,
        "unknown": unknown,
    }
    if targetless is not None:
        event["targetless"] = targetless
    if phase == "post":
        event["succeeded"] = succeeded
    return event

def _active_outcome(root: Path, event: hookproto.Event) -> hookproto.Outcome:
    return cli._hook_event_outcome(
        cli.HookRepository(cli.HookRepositoryState.ACTIVE, root=root),
        event,
    )


def _write_target(root: Path, rel: str, content: str) -> dict:
    return {
        "path": str(root / rel),
        "op": "write",
        "expectedPresent": True,
        "content": content,
    }


def _edit_target(
    root: Path,
    rel: str,
    added: list[str],
    removed: list[str] | None = None,
    ambiguous: bool = False,
) -> dict:
    return {
        "path": str(root / rel),
        "op": "edit",
        "expectedPresent": True,
        "added": added,
        "removed": removed or [],
        "ambiguous": ambiguous,
    }

def _strict_edit_request(ambiguous: object = True) -> dict:
    target = _edit_target(Path.cwd(), "a.py", ["x"])
    target["ambiguous"] = ambiguous
    return _event("pre", Path.cwd(), "edit", [target])


# The two shapes the manifest gate sees: a full-content write, and an edit that
# reports only the fragments it inserts.
def _manifest_write(root: Path, content: str) -> dict:
    return _event("pre", root, "write", [_write_target(root, ".tackbox/approvals", content)])


def _manifest_edit(root: Path, added: list[str], **kw) -> dict:
    return _event("pre", root, "edit", [_edit_target(root, ".tackbox/approvals", added, **kw)])


def _decision(r: subprocess.CompletedProcess) -> dict:
    assert r.returncode == 0, f"a reached decision always exits 0:\n{r.stdout}\n{r.stderr}"
    assert r.stdout.strip().count("\n") == 0, f"expected one decision object:\n{r.stdout}"
    payload = json.loads(r.stdout)
    assert payload["protocol"] == 1, payload
    return payload


def _decide(event: dict) -> dict:
    return _decision(_run(json.dumps(event)))


def _no_decision(payload: str) -> subprocess.CompletedProcess:
    r = _run(payload)
    assert r.returncode == 1, f"an unspeakable request exits 1:\n{r.stdout}\n{r.stderr}"
    assert r.stdout == "", f"no decision may be printed:\n{r.stdout}"
    assert r.stderr.strip() != "", "one stderr line expected"
    assert r.stderr.strip().count("\n") == 0, f"exactly one line:\n{r.stderr}"
    assert "Traceback" not in r.stderr, r.stderr
    return r


# -- protocol validation: no decision, never a traceback


def test_unsupported_version_is_no_decision(tmp_path):
    _repo(tmp_path)
    for unsupported in (2, True, 1.0):
        event = _event("pre", tmp_path, "edit", [])
        event["protocol"] = unsupported
        r = _no_decision(json.dumps(event))
        assert f"unsupported protocol {unsupported!r}" in r.stderr, r.stderr


def test_unknown_phase_is_no_decision(tmp_path):
    _repo(tmp_path)
    event = _event("during", tmp_path, "edit", [])
    r = _no_decision(json.dumps(event))
    assert "phase must be" in r.stderr, r.stderr


def test_malformed_target_is_no_decision(tmp_path):
    _repo(tmp_path)
    event = _event("pre", tmp_path, "edit", [_edit_target(tmp_path, "a.py", [7])])
    r = _no_decision(json.dumps(event))
    assert "must be a list of strings" in r.stderr, r.stderr

def test_relative_cwd_is_no_decision(tmp_path):
    _repo(tmp_path)
    event = _event("pre", tmp_path, "edit", [])
    event["cwd"] = "relative"
    r = _no_decision(json.dumps(event))
    assert "cwd must be absolute" in r.stderr, r.stderr


def test_relative_target_is_no_decision(tmp_path):
    _repo(tmp_path)
    event = _event("pre", tmp_path, "edit", [{"path": "relative.py"}])
    r = _no_decision(json.dumps(event))
    assert "target.path must be absolute" in r.stderr, r.stderr


def test_empty_unknown_reason_is_no_decision(tmp_path):
    _repo(tmp_path)
    event = _event("pre", tmp_path, "edit", [])
    event["unknown"] = ""
    r = _no_decision(json.dumps(event))
    assert "unknown must be a non-empty string" in r.stderr, r.stderr


def test_broken_stdin_is_no_decision():
    r = _no_decision("this is not json {")
    assert "unreadable stdin" in r.stderr, r.stderr


# -- the guard


def test_cwd_outside_git_allows_silently(tmp_path):
    # tmp_path is not a git repo: the hook is a deliberate no-op everywhere it
    # is not wired in, and a no-op is still a decision.
    payload = _decide(_event("pre", tmp_path, "edit", [_edit_target(tmp_path, "a.py", ["x = 1"])]))
    assert payload == {"protocol": 1, "decision": "allow", "reason": ""}


def test_git_without_devpy_allows(tmp_path):
    init_repo(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    payload = _decide(_manifest_write(tmp_path, MANIFEST_ENTRY + "\n"))
    assert payload["decision"] == "allow", payload


# -- Pre: the approval gates


def test_pre_write_manifest_entry_asks(tmp_path):
    # An exact full-content write keeps the Claude-host behavior: the added line
    # is the disk-vs-content difference, quoted verbatim.
    _repo(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    payload = _decide(_manifest_write(tmp_path, MANIFEST_ENTRY + "\n"))
    assert payload["decision"] == "ask", payload
    assert payload["reason"] == CANON_SINGLE


def test_pre_write_manifest_removal_is_free(tmp_path):
    _repo(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox" / "approvals").write_text(
        f"{MANIFEST_ENTRY}\nb.py: no-report: second approved marker\n"
    )
    payload = _decide(_manifest_write(tmp_path, MANIFEST_ENTRY + "\n"))
    assert payload["decision"] == "allow", payload


def test_pre_edit_added_fragment_asks(tmp_path):
    # An edit reports the text it inserts, not a whole file: one added manifest
    # line still draws the canonical ask.
    _repo(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox" / "approvals").write_text("b.py: no-report: already approved\n")
    payload = _decide(_manifest_edit(tmp_path, [MANIFEST_ENTRY]))
    assert payload["reason"] == CANON_SINGLE


def test_pre_edit_removal_only_is_free(tmp_path):
    # A removal-only edit (a cut, a delete) adds nothing, so it needs no approval.
    _repo(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox" / "approvals").write_text(f"{MANIFEST_ENTRY}\n")
    payload = _decide(_manifest_edit(tmp_path, [], removed=[MANIFEST_ENTRY]))
    assert payload["decision"] == "allow", payload


def test_pre_unclassifiable_gated_edit_asks(tmp_path):
    # ADVERSARIAL: a register paste or a move into the manifest enumerates no
    # added line at all. The gate must ask rather than read that as "adds
    # nothing" - the one direction that would silently widen the bypass surface.
    _repo(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    payload = _decide(_manifest_edit(tmp_path, [], ambiguous=True))
    assert payload["decision"] == "ask", payload
    assert "cannot classify what this edit adds" in payload["reason"], payload
    assert ".tackbox/approvals" in payload["reason"], payload


def test_pre_unknown_payload_blocks_with_its_reason(tmp_path):
    _repo(tmp_path)
    reason = "tackbox cannot classify this edit call (no known field)"
    payload = _decide(_event("pre", tmp_path, "edit", [], unknown=reason))
    assert payload == {"protocol": 1, "decision": "block", "reason": reason}


def test_pre_excluded_target_asks(tmp_path):
    _repo(tmp_path)
    (tmp_path / ".gitattributes").write_text("gen/*.pb.go linguist-generated\n")
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "api.pb.go").write_text("package gen\n")
    payload = _decide(
        _event("pre", tmp_path, "edit",
               [_edit_target(tmp_path, "gen/api.pb.go", ["// touched"])])
    )
    assert payload["reason"] == "edit attribute-excluded file (linguist-generated): gen/api.pb.go"


def test_pre_gitattributes_exclusion_line_asks(tmp_path):
    _repo(tmp_path)
    payload = _decide(
        _event("pre", tmp_path, "edit",
               [_edit_target(tmp_path, ".gitattributes", ["gen/*.pb.go linguist-generated"])])
    )
    assert payload["reason"] == (
        ".gitattributes exclusion line added: gen/*.pb.go linguist-generated"
    )


def test_pre_plain_edit_is_free(tmp_path):
    _repo(tmp_path)
    payload = _decide(
        _event("pre", tmp_path, "edit", [_edit_target(tmp_path, "svc.py", ["x = 2"])])
    )
    assert payload == {"protocol": 1, "decision": "allow", "reason": ""}


def test_pre_multi_file_call_asks_once_for_every_reason(tmp_path):
    # A multi-file edit is approved or rejected atomically, so one ask lists
    # every gated file it touches.
    _repo(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".gitattributes").write_text("gen/** linguist-generated\n")
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "api.py").write_text("x = 1\n")
    payload = _decide(
        _event("pre", tmp_path, "edit", [
            _edit_target(tmp_path, ".tackbox/approvals", [MANIFEST_ENTRY]),
            _edit_target(tmp_path, "gen/api.py", ["x = 2"]),
            _edit_target(tmp_path, "plain.py", ["y = 2"]),
        ])
    )
    assert payload["decision"] == "ask", payload
    assert payload["reason"] == (
        f"{CANON_SINGLE}\n"
        "edit attribute-excluded file (linguist-generated): gen/api.py"
    )


def test_pre_ask_text_matches_the_claude_host(tmp_path):
    # The binding invariant: both hosts render ONE shared decision, so the same
    # change draws byte-identical text through either wire.
    _repo(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    content = MANIFEST_ENTRY + "\n"
    protocol = _decide(_manifest_write(tmp_path, content))
    claude = subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "hook"],
        input=json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "cwd": str(tmp_path),
            "tool_input": {
                "file_path": str(tmp_path / ".tackbox" / "approvals"),
                "content": content,
            },
        }),
        cwd=TACKBOX_ROOT,
        env=tackbox_env(),
        capture_output=True,
        text=True,
    )
    assert claude.returncode == 0, claude.stderr
    reason = json.loads(claude.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert protocol["reason"] == reason == CANON_SINGLE


# -- Post: the consistency wall and the diff-scoped lint arm


@pytest.mark.parametrize("tool", ["bash", "eval"])
def test_post_target_free_channel_runs_the_whole_tree_wall(tmp_path, tool):
    # Target-free channels must still exercise the worktree wall.
    _repo(tmp_path)
    (tmp_path / "svc.py").write_text("# no-report: shelled in at module scope\nx = 1\n")
    payload = _decide(_event("post", tmp_path, tool, []))
    assert payload["decision"] == "block", payload
    assert "Unapproved suppression marker" in payload["reason"], payload
    assert "svc.py: no-report: shelled in at module scope" in payload["reason"], payload


def test_post_clean_tree_allows(tmp_path):
    _repo(tmp_path)
    (tmp_path / "svc.py").write_text("x = 1\n")
    payload = _decide(_event("post", tmp_path, "bash", []))
    assert payload == {"protocol": 1, "decision": "allow", "reason": ""}


def test_post_finding_on_touched_lines_blocks(tmp_path):
    target = tmp_path / "src" / "app.svelte"
    target.parent.mkdir()
    target.write_text(SVELTE_SWALLOW)
    _repo(tmp_path)
    payload = _decide(
        _event("post", tmp_path, "write",
               [_write_target(tmp_path, "src/app.svelte", SVELTE_SWALLOW)])
    )
    assert payload["decision"] == "block", payload
    assert "src/app.svelte:2" in payload["reason"], payload
    assert "tackbox/no-swallow-catch" in payload["reason"], payload


def test_post_finding_off_the_touched_lines_allows(tmp_path):
    # Diff-scope: the edit's own added text is clean, so a finding elsewhere in
    # the file is `dev.py check`'s business, not this event's.
    target = tmp_path / "src" / "app.svelte"
    target.parent.mkdir()
    target.write_text("<script>\ntry { f() } catch (e) {}\nconst kept = 1\n</script>\n")
    _repo(tmp_path)
    payload = _decide(
        _event("post", tmp_path, "edit",
               [_edit_target(tmp_path, "src/app.svelte", ["const kept = 1"])])
    )
    assert payload["decision"] == "allow", payload


def test_post_multi_file_edit_reports_every_file(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "one.svelte").write_text(SVELTE_SWALLOW)
    (tmp_path / "src" / "two.svelte").write_text(SVELTE_SWALLOW)
    _repo(tmp_path)
    payload = _decide(
        _event("post", tmp_path, "edit", [
            _edit_target(tmp_path, "src/one.svelte", ["try { f() } catch (e) {}"]),
            _edit_target(tmp_path, "src/two.svelte", ["try { f() } catch (e) {}"]),
        ])
    )
    assert payload["decision"] == "block", payload
    assert "src/one.svelte:2" in payload["reason"], payload
    assert "src/two.svelte:2" in payload["reason"], payload
    assert "pre-existing elsewhere" not in payload["reason"], payload


def test_post_declared_delete_is_not_an_error(tmp_path):
    _repo(tmp_path)
    target = {
        "path": str(tmp_path / "gone.py"),
        "op": "delete",
        "expectedPresent": False,
        "removed": ["x = 1"],
    }
    payload = _decide(_event("post", tmp_path, "edit", [target]))
    assert payload["decision"] == "allow", payload


def test_post_declared_move_skips_absent_source_and_lints_destination(tmp_path):
    target = tmp_path / "src" / "moved.svelte"
    target.parent.mkdir()
    target.write_text(SVELTE_SWALLOW)
    _repo(tmp_path)
    pair = "old-to-moved"
    payload = _decide(
        _event("post", tmp_path, "edit", [
            {
                "path": str(tmp_path / "src" / "old.svelte"),
                "op": "move",
                "expectedPresent": False,
                "moveId": pair,
            },
            {
                "path": str(target),
                "op": "move",
                "expectedPresent": True,
                "content": SVELTE_SWALLOW,
                "moveId": pair,
            },
        ])
    )
    assert payload["decision"] == "block", payload
    assert "src/moved.svelte:2" in payload["reason"], payload
def test_post_unknown_payload_warns_without_blocking(tmp_path):
    # The mutation already landed and the tree is consistent; what could not be
    # checked is said out loud, and the turn keeps running.
    _repo(tmp_path)
    (tmp_path / "svc.py").write_text("x = 1\n")
    reason = "tackbox cannot classify this edit call (no known field)"
    payload = _decide(_event("post", tmp_path, "edit", [], unknown=reason))
    assert payload["decision"] == "warn", payload
    assert payload["reason"] == (
        "The mutation may already have landed.\n"
        f"Tackbox verification did not complete: tackbox hook: {reason}\n"
        "Do not repeat the mutation; dev.py check remains required."
    )


def test_post_wall_keeps_unknown_verification_failure(tmp_path):
    _repo(tmp_path)
    (tmp_path / "svc.py").write_text("# no-report: unapproved marker\nx = 1\n")
    reason = "tackbox cannot classify this edit call"
    payload = _decide(_event("post", tmp_path, "edit", [], unknown=reason))
    assert payload["decision"] == "block", payload
    assert "Unapproved suppression marker" in payload["reason"], payload
    assert f"verification uncertainty: tackbox hook: {reason}" in payload["reason"], payload


# -- unit: the event model and path handling


def test_parse_request_requires_post_success_flag():
    with pytest.raises(hookproto.HookProtocolError, match="post.succeeded"):
        hookproto.parse_request({
            "protocol": 1,
            "phase": "post",
            "cwd": str(Path.cwd()),
            "tool": "bash",
            "targets": [],
            "unknown": None,
            "targetless": "opaque",
        })


def test_parse_request_reads_a_target():
    absolute = Path.cwd() / "a.py"
    event = hookproto.parse_request(_strict_edit_request())
    target = event.targets[0]
    assert target.path == absolute
    assert target.added == ("x",) and target.removed == ()
    assert target.ambiguous is True and target.content is None


def test_parse_request_rejects_malformed_ambiguous_flag():
    with pytest.raises(hookproto.HookProtocolError, match="ambiguous"):
        hookproto.parse_request(_strict_edit_request(0))


def test_render_decision_is_one_json_line():
    line = hookproto.render_decision(
        hookproto.Outcome(hookproto.OutcomeKind.VIOLATION, "a\nb"),
        "post",
    )
    assert "\n" not in line
    assert json.loads(line) == {"protocol": 1, "decision": "block", "reason": "a\nb"}


def test_post_scope_maps_native_paths_to_posix_rels(tmp_path):
    # A host hands over its own absolute paths - backslashed on Windows - and the
    # source set is addressed the way git spells it, on every platform.
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "app.js"
    target.write_text("const a = 1\nconst b = 2\n")
    event = hookproto.Event(
        phase="post",
        cwd=str(tmp_path),
        tool="edit",
        targets=(hookproto.Target(Path(str(target)), added=("const b = 2",)),),
    )
    scope = cli._post_scope(tmp_path, event)
    assert scope.files == {"src/app.js": {2}} and scope.failures == ()


def test_post_scope_drops_targets_outside_the_repo(tmp_path):
    outside = tmp_path.parent / "not-in-repo.py"
    event = hookproto.Event(
        phase="post",
        cwd=str(tmp_path),
        tool="edit",
        targets=(hookproto.Target(outside, added=("x = 1",)),),
    )
    scope = cli._post_scope(tmp_path, event)
    assert scope.files == {} and scope.failures == ()


def test_post_scope_widens_a_repeated_path_to_the_whole_file(tmp_path):
    target = tmp_path / "app.js"
    target.write_text("const a = 1\n")
    event = hookproto.Event(
        phase="post",
        cwd=str(tmp_path),
        tool="edit",
        targets=(
            hookproto.Target(target, added=("const a = 1",)),
            hookproto.Target(target, ambiguous=True),
        ),
    )
    scope = cli._post_scope(tmp_path, event)
    assert scope.files == {"app.js": None} and scope.failures == ()


def test_affected_lines_are_whole_file_for_a_full_write(tmp_path):
    target = tmp_path / "app.js"
    target.write_text("const a = 1\n")
    assert cli._affected_for(hookproto.Target(target, content="const a = 1\n"), "app.js") == (
        None,
        None,
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda event: event.update(cwd=None), "cwd must be"),
        (lambda event: event.update(tool="Edit"), "tool must be"),
        (
            lambda event: event["targets"][0].update(expectedPresent=1),
            "expectedPresent",
        ),
        (
            lambda event: event["targets"][0].update(added=["x"]),
            "mutually exclusive",
        ),
        (
            lambda event: event.update(
                unknown="cannot classify", targets=[
                    _edit_target(Path(event["cwd"]), "a.py", ["x"])
                ],
            ),
            "cannot accompany",
        ),
    ],
)
def test_protocol_rejects_contradictory_host_fields(tmp_path, mutate, message):
    event = _event(
        "pre",
        tmp_path,
        "write",
        [_write_target(tmp_path, "a.py", "x = 1\n")],
    )
    mutate(event)
    result = _no_decision(json.dumps(event))
    assert message in result.stderr
@pytest.mark.parametrize("tool", ["bash", "eval"])
def test_protocol_rejects_opaque_tool_with_concrete_targets(tool):
    root = Path.cwd()
    with pytest.raises(hookproto.HookProtocolError, match="bash and eval"):
        hookproto.parse_request(
            _event("pre", root, tool, [_edit_target(root, "a.py", ["x"])])
        )


def test_protocol_rejects_write_delete_target():
    root = Path.cwd()
    delete = {
        "path": str(root / "gone.py"),
        "op": "delete",
        "expectedPresent": False,
    }
    with pytest.raises(hookproto.HookProtocolError, match="write requests"):
        hookproto.parse_request(_event("pre", root, "write", [delete]))


def test_protocol_requires_a_paired_move_destination():
    root = Path.cwd()
    source = {
        "path": str(root / "from.py"),
        "op": "move",
        "expectedPresent": False,
        "moveId": "pair",
    }
    with pytest.raises(hookproto.HookProtocolError, match="move pair"):
        hookproto.parse_request(_event("pre", root, "edit", [source]))


def test_protocol_accepts_a_pruned_delete_wire_target():
    root = Path.cwd()
    delete = {
        "path": str(root / "gone.py"),
        "op": "delete",
        "expectedPresent": False,
    }
    event = hookproto.parse_request(_event("post", root, "edit", [delete]))
    assert event.targets[0].operation == hookproto.DELETE
    assert event.targets[0].expected_present is False


def test_repository_discovery_distinguishes_inactive_from_missing_git(tmp_path, monkeypatch):
    inactive = cli._hook_repository(str(tmp_path))
    assert inactive.state is cli.HookRepositoryState.INACTIVE

    def missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(cli.proc, "run", missing_git)
    broken = cli._hook_repository(str(tmp_path))
    assert broken.state is cli.HookRepositoryState.INFRASTRUCTURE_FAILURE
    assert "cannot discover hook repository" in broken.reason


def test_repository_discovery_uses_c_locale_non_repository_stderr(
    tmp_path, monkeypatch
):
    observed = {}

    def non_repository(argv, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            argv,
            128,
            stdout="",
            stderr="fatal: not a git repository (or any of the parent directories): .git\n",
        )

    monkeypatch.setattr(cli.proc, "run", non_repository)
    result = cli._hook_repository(str(tmp_path))
    assert result.state is cli.HookRepositoryState.INACTIVE
    assert observed["env"]["LC_ALL"] == "C"
    assert observed["env"]["LANG"] == "C"


def test_repository_discovery_corrupt_git_config_is_unverified(tmp_path, monkeypatch):
    stderr = "fatal: bad config line 1 in file .git/config\n"

    def broken_git(argv, **kwargs):
        assert kwargs["env"]["LC_ALL"] == kwargs["env"]["LANG"] == "C"
        return subprocess.CompletedProcess(argv, 128, stdout="", stderr=stderr)

    monkeypatch.setattr(cli.proc, "run", broken_git)
    result = cli._hook_repository(str(tmp_path))
    assert result.state is cli.HookRepositoryState.INFRASTRUCTURE_FAILURE
    assert "git rev-parse failed" in result.reason
    assert stderr.strip() in result.reason


@pytest.mark.parametrize("operation", ["delete", "move"])
def test_pre_root_devpy_removal_and_move_ask(tmp_path, operation):
    _repo(tmp_path)
    source = {
        "path": str(tmp_path / "dev.py"),
        "op": operation,
        "expectedPresent": False,
    }
    targets = [source]
    if operation == "move":
        pair = "root-dev-py"
        source["moveId"] = pair
        targets.append({
            "path": str(tmp_path / "tools" / "dev.py"),
            "op": "move",
            "expectedPresent": True,
            "ambiguous": True,
            "moveId": pair,
        })
    else:
        source["removed"] = []
    payload = _decide(_event("pre", tmp_path, "edit", targets))
    assert payload["decision"] == "ask"
    assert "root dev.py" in payload["reason"]


def test_post_root_devpy_removal_stays_unverified_after_guard_disables(tmp_path):
    _repo(tmp_path)
    (tmp_path / "dev.py").unlink()
    target = {
        "path": str(tmp_path / "dev.py"),
        "op": "delete",
        "expectedPresent": False,
        "removed": [],
    }
    payload = _decide(_event("post", tmp_path, "edit", [target]))
    assert payload["decision"] == "warn"
    assert "root dev.py was removed or moved" in payload["reason"]


def test_pre_utf16_gated_full_replacement_blocks_as_unverified(tmp_path):
    _repo(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    approvals_path = tmp_path / ".tackbox" / "approvals"
    approvals_path.write_text(MANIFEST_ENTRY + "\n", encoding="utf-16")
    payload = _decide(_manifest_write(tmp_path, MANIFEST_ENTRY + "\n"))
    assert payload["decision"] == "block"
    assert "cannot read" in payload["reason"]


def test_pre_unreadable_gated_full_replacement_is_unverified(tmp_path, monkeypatch):
    _repo(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox" / "approvals").write_text("", encoding="utf-8")
    target = hookproto.Target(
        tmp_path / ".tackbox" / "approvals",
        operation=hookproto.WRITE,
        content=MANIFEST_ENTRY + "\n",
    )
    event = hookproto.Event(
        phase=hookproto.PRE,
        cwd=str(tmp_path),
        tool="write",
        targets=(target,),
    )

    def unreadable(*args, **kwargs):
        raise OSError("access denied")

    monkeypatch.setattr(Path, "read_text", unreadable)
    outcome = _active_outcome(tmp_path, event)
    assert outcome.kind is hookproto.OutcomeKind.UNVERIFIED
    assert "access denied" in outcome.reason


def test_missing_added_fragment_widens_to_whole_file_with_explicit_failure(tmp_path):
    target = tmp_path / "a.js"
    target.write_text("const actual = true\n")
    event = hookproto.Event(
        phase=hookproto.POST,
        cwd=str(tmp_path),
        tool="edit",
        targets=(hookproto.Target(target, added=("const missing = true",)),),
    )
    scope = cli._post_scope(tmp_path, event)
    assert scope.files == {"a.js": None}
    assert scope.failures == (
        "added fragment was not found in landed a.js; lint widened to the whole file",
    )


def test_missing_expected_post_file_is_unverified_but_failed_edit_is_not(tmp_path):
    _repo(tmp_path)
    missing = _write_target(tmp_path, "missing.py", "x = 1\n")
    landed = _decide(_event("post", tmp_path, "write", [missing]))
    assert landed["decision"] == "warn"
    assert "expected post file is absent" in landed["reason"]
    failed = _event(
        "post",
        tmp_path,
        "write",
        [],
        targetless="failed",
        succeeded=False,
    )
    assert _decide(failed) == {"protocol": 1, "decision": "allow", "reason": ""}
def test_unsuccessful_post_scopes_explicitly_landed_targets(tmp_path):
    target = tmp_path / "src" / "app.svelte"
    target.parent.mkdir()
    target.write_text(SVELTE_SWALLOW)
    _repo(tmp_path)
    payload = _decide(
        _event(
            "post",
            tmp_path,
            "apply_patch",
            [_edit_target(tmp_path, "src/app.svelte", ["try { f() } catch (e) {}"])],
            succeeded=False,
        )
    )
    assert payload["decision"] == "block", payload
    assert "src/app.svelte:2" in payload["reason"], payload


def test_ambiguous_known_included_target_allows_while_unknown_target_blocks(tmp_path):
    _repo(tmp_path)
    known = _decide(
        _event("pre", tmp_path, "edit", [_edit_target(tmp_path, "a.py", [], ambiguous=True)])
    )
    assert known == {"protocol": 1, "decision": "allow", "reason": ""}
    unknown = _decide(_event("pre", tmp_path, "edit", [], unknown="unknown target"))
    assert unknown == {"protocol": 1, "decision": "block", "reason": "unknown target"}


def test_excluded_target_with_unavailable_attribute_child_is_unverified(tmp_path, monkeypatch):
    _repo(tmp_path)
    (tmp_path / ".gitattributes").write_text("gen/** linguist-generated\n")
    (tmp_path / "gen").mkdir()
    target = hookproto.Target(tmp_path / "gen" / "api.py", added=("x = 1",))
    event = hookproto.Event(
        phase=hookproto.PRE,
        cwd=str(tmp_path),
        tool="edit",
        targets=(target,),
    )
    def unavailable(*args, **kwargs):
        raise cli.AttributeResolutionError("git check-attr unavailable")

    monkeypatch.setattr(cli, "resolve_attributes", unavailable)
    outcome = _active_outcome(tmp_path, event)
    assert outcome.kind is hookproto.OutcomeKind.UNVERIFIED
    assert "git check-attr unavailable" in outcome.reason

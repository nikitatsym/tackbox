"""Behavioral coverage of the host-neutral hook command and its shared core."""

from __future__ import annotations

import json
import subprocess
import sys
import pytest
from pathlib import Path

from conftest import commit_all, git, init_repo, tackbox_env

from tackbox import cli, hookproto

TACKBOX_ROOT = Path(__file__).resolve().parents[2]

SVELTE_SWALLOW = "<script>\ntry { f() } catch (e) {}\n</script>\n"
SVELTE_CLEAN = "<script>\nexport let name = 'x'\n</script>\n"

MANIFEST_ENTRY = "app/svc.py#Handler.process: no-report: legacy path, covered upstream"


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
    assert MANIFEST_ENTRY in payload["reason"]


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
    assert payload["decision"] == "ask"
    assert MANIFEST_ENTRY in payload["reason"]


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
    assert payload["decision"] == "ask"
    assert "linguist-generated" in payload["reason"] and "gen/api.pb.go" in payload["reason"]


def test_pre_gitattributes_exclusion_line_asks(tmp_path):
    _repo(tmp_path)
    payload = _decide(
        _event("pre", tmp_path, "edit",
               [_edit_target(tmp_path, ".gitattributes", ["gen/*.pb.go linguist-generated"])])
    )
    assert payload["decision"] == "ask"
    assert "gen/*.pb.go linguist-generated" in payload["reason"]


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
    assert MANIFEST_ENTRY in payload["reason"]
    assert "linguist-generated" in payload["reason"] and "gen/api.py" in payload["reason"]


# -- Post: session debt and diff-scoped lint


@pytest.mark.parametrize("phase", ["pre", "post"])
@pytest.mark.parametrize("tool", ["bash", "eval"])
def test_target_free_channel_ignores_approvals(tmp_path, phase, tool):
    _repo(tmp_path)
    (tmp_path / "svc.py").write_text("# no-report: shelled in at module scope\nx = 1\n")
    payload = _decide(_event(phase, tmp_path, tool, []))
    assert payload == {"protocol": 1, "decision": "allow", "reason": ""}


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
    assert reason in payload["reason"]
    assert "mutation may already have landed" in payload["reason"]
    assert "Do not repeat" in payload["reason"] and "dev.py check" in payload["reason"]


def test_post_unknown_mutation_does_not_attribute_unrelated_debt(tmp_path):
    _repo(tmp_path)
    (tmp_path / "svc.py").write_text("# no-report: unapproved marker\nx = 1\n")
    reason = "tackbox cannot classify this edit call"
    payload = _decide(_event("post", tmp_path, "edit", [], unknown=reason))
    assert payload["decision"] == "warn", payload
    assert reason in payload["reason"]
    assert "svc.py" not in payload["reason"]


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


def test_post_scope_refuses_targets_outside_its_repository(tmp_path):
    outside = tmp_path.parent / "not-in-repo.py"
    event = hookproto.Event(
        phase="post",
        cwd=str(tmp_path),
        tool="edit",
        targets=(hookproto.Target(outside, added=("x = 1",)),),
    )
    scope = cli._post_scope(tmp_path, event)
    assert scope.files == {}
    assert len(scope.failures) == 1
    assert "outside repository" in scope.failures[0]


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
    outcome = cli._hook_outcome(event)
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
    outcome = cli._hook_outcome(event)
    assert outcome.kind is hookproto.OutcomeKind.UNVERIFIED
    assert "git check-attr unavailable" in outcome.reason


def test_session_debt_blocks_only_outside_the_fix_set(tmp_path):
    _repo(tmp_path)
    (tmp_path / "debt.js").write_text("// no-report: old debt belongs to CI\ntry { f() } catch (e) {}\n")
    (tmp_path / "other.js").write_text("const value = 1\n")
    commit_all(tmp_path)
    other = _edit_target(tmp_path, "other.js", ["const value = 2"])
    assert _decide(_event("pre", tmp_path, "edit", [other]))["decision"] == "allow"
    (tmp_path / "other.js").write_text("const value = 2\n")
    assert _decide(_event("post", tmp_path, "edit", [other]))["decision"] == "allow"
    (tmp_path / "new.js").write_text("// no-report: caller tolerates this failure\ntry { f() } catch (e) {}\n")
    git(tmp_path, "add", "new.js")
    blocked = _decide(_event("pre", tmp_path, "edit", [other]))
    assert blocked["decision"] == "block"
    assert len(blocked["reason"].splitlines()) == 1
    assert "new.js:1:" in blocked["reason"] and "every catch path must throw" in blocked["reason"]
    assert "#<h" not in blocked["reason"] and "old debt" not in blocked["reason"]
    marker = _edit_target(tmp_path, "new.js", ["throw e"])
    assert _decide(_event("pre", tmp_path, "edit", [marker]))["decision"] == "allow"
    repair = _edit_target(tmp_path, ".tackbox/approvals", [], removed=["obsolete"])
    assert _decide(_event("pre", tmp_path, "edit", [repair]))["decision"] == "allow"
    assert _decide(_event("pre", tmp_path, "edit", [marker, other]))["decision"] == "block"
    assert _decide(_event("post", tmp_path, "edit", [other]))["decision"] == "allow"
    posted = _decide(_event("post", tmp_path, "write", [
        _write_target(tmp_path, "new.js", (tmp_path / "new.js").read_text())
    ]))
    assert posted["decision"] == "block" and "every catch path must throw" in posted["reason"]
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox/approvals").write_text("new.js: no-report: caller tolerates this failure\n")
    assert _decide(_event("pre", tmp_path, "edit", [other]))["decision"] == "allow"


@pytest.mark.parametrize("delete_file", [False, True])
def test_deleted_marker_with_retained_approval_creates_session_debt(tmp_path, delete_file):
    _repo(tmp_path)
    marker = "# no-report: caller tolerates this failure\n"
    (tmp_path / "marker.py").write_text(marker + "x = 1\n")
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox/approvals").write_text("marker.py: no-report: caller tolerates this failure\n")
    commit_all(tmp_path)
    if delete_file:
        (tmp_path / "marker.py").unlink()
    else:
        (tmp_path / "marker.py").write_text("x = 1\n")
    target = _edit_target(tmp_path, "other.py", ["x = 1"])
    blocked = _decide(_event("pre", tmp_path, "edit", [target]))
    assert blocked["decision"] == "block"
    assert blocked["reason"] == ".tackbox/approvals:1: approval has no matching marker: remove the line"
    (tmp_path / ".tackbox/approvals").write_text("")
    assert _decide(_event("pre", tmp_path, "edit", [target]))["decision"] == "allow"


def test_added_orphan_and_unborn_tree_are_session_debt(tmp_path):
    (tmp_path / "dev.py").write_text("# hook entry\n")
    init_repo(tmp_path)
    (tmp_path / "new.py").write_text("try:\n    work()\nexcept ValueError:\n    # no-report: caller tolerates this failure\n    pass\n")
    other = _edit_target(tmp_path, "other.py", ["x = 1"])
    blocked = _decide(_event("pre", tmp_path, "edit", [other]))
    assert blocked["decision"] == "block" and "let the exception propagate" in blocked["reason"]
    (tmp_path / "new.py").write_text("x = 1\n")
    commit_all(tmp_path)
    (tmp_path / ".tackbox").mkdir()
    (tmp_path / ".tackbox/approvals").write_text("missing.py: no-report: nonexistent occurrence\n")
    blocked = _decide(_event("pre", tmp_path, "edit", [other]))
    assert blocked["decision"] == "block" and ".tackbox/approvals:1:" in blocked["reason"]


@pytest.mark.parametrize("duplicate", ["marker", "approval"])
def test_session_debt_assigns_duplicate_capacity_to_unchanged_lines_first(tmp_path, duplicate):
    _repo(tmp_path)
    marker = "# no-report: caller tolerates this failure\n"
    entry = "marker.py: no-report: caller tolerates this failure\n"
    original = "x = 1\n" + marker + "y = 2\n"
    (tmp_path / "marker.py").write_text(original)
    (tmp_path / ".tackbox").mkdir()
    manifest = tmp_path / ".tackbox/approvals"
    manifest.write_text("\n" + entry)
    commit_all(tmp_path)
    if duplicate == "marker":
        (tmp_path / "marker.py").write_text(marker + original)
        expected = "marker.py:1: unapproved no-report marker"
    else:
        manifest.write_text(entry + "\n" + entry)
        expected = ".tackbox/approvals:1: approval has no matching marker"
    other = _edit_target(tmp_path, "other.py", ["x = 1"])
    blocked = _decide(_event("pre", tmp_path, "edit", [other]))
    assert blocked["decision"] == "block"
    assert blocked["reason"].startswith(expected)
    assert len(blocked["reason"].splitlines()) == 1


def _child_repo(parent: Path, name: str) -> Path:
    root = parent / name
    root.mkdir()
    _repo(root)
    return root


@pytest.mark.parametrize("cwd_kind", ["outside", "inactive", "active", "broken"])
def test_manifest_policy_follows_target_not_launch_repository(tmp_path, cwd_kind):
    target_root = _child_repo(tmp_path, "target")
    cwd = tmp_path
    if cwd_kind != "outside":
        cwd = _child_repo(tmp_path, "launch")
        if cwd_kind == "inactive":
            (cwd / "dev.py").unlink()
        elif cwd_kind == "broken":
            (cwd / ".git/config").write_text("[invalid\n")
    event = _event("pre", cwd, "write", [
        _write_target(target_root, ".tackbox/approvals", MANIFEST_ENTRY + "\n"),
    ])
    decision = _decide(event)
    assert decision["decision"] == "ask"
    assert MANIFEST_ENTRY in decision["reason"]


def test_multi_repository_asks_preserve_each_repository_and_entry(tmp_path):
    first = _child_repo(tmp_path, "first")
    second = _child_repo(tmp_path, "second")
    second_entry = "other.py: no-report: second repository exception"
    event = _event("pre", tmp_path, "edit", [
        _edit_target(first, ".tackbox/approvals", [MANIFEST_ENTRY]),
        _edit_target(second, ".tackbox/approvals", [second_entry]),
    ])
    decision = _decide(event)
    assert decision["decision"] == "ask"
    for value in (str(first), str(second), MANIFEST_ENTRY, second_entry):
        assert value in decision["reason"]


@pytest.mark.parametrize("phase", ["pre", "post"])
def test_broken_target_repository_cannot_hide_other_repository_approval(tmp_path, phase):
    gated = _child_repo(tmp_path, "gated")
    broken = _child_repo(tmp_path, "broken")
    (broken / ".git/config").write_text("[invalid\n")
    if phase == "post":
        (gated / "plain.py").write_text("x = 1\n")
    gated_target = _edit_target(gated, ".tackbox/approvals", [MANIFEST_ENTRY]) if phase == "pre" else _edit_target(gated, "plain.py", ["x = 1"])
    decision = _decide(_event(phase, tmp_path, "edit", [
        gated_target, _edit_target(broken, "plain.py", ["x = 1"]),
    ]))
    assert decision["decision"] == ("block" if phase == "pre" else "warn")
    assert str(broken / "plain.py") in decision["reason"]
    assert "bad config" in decision["reason"]
    if phase == "pre":
        assert MANIFEST_ENTRY in decision["reason"]
    else:
        assert "Do not repeat the mutation" in decision["reason"]


def test_multi_repository_post_preserves_findings_and_discovery_failure(tmp_path):
    landed = _child_repo(tmp_path, "landed")
    broken = _child_repo(tmp_path, "broken")
    (landed / "bad.svelte").write_text(SVELTE_SWALLOW)
    (broken / ".git/config").write_text("[invalid\n")
    decision = _decide(_event("post", tmp_path, "edit", [
        _write_target(landed, "bad.svelte", SVELTE_SWALLOW),
        _edit_target(broken, "plain.py", ["x = 1"]),
    ]))
    assert decision["decision"] == "block"
    assert "bad.svelte:2" in decision["reason"]
    assert "tackbox/no-swallow-catch" in decision["reason"]
    assert "bad config" in decision["reason"]
    assert "Do not repeat the mutation" in decision["reason"]


def test_worktree_policy_uses_worktree_tree_not_main_checkout(tmp_path):
    main = _child_repo(tmp_path, "main")
    worktree = tmp_path / "worktree"
    git(main, "worktree", "add", "-q", "-b", "linked", str(worktree))
    (main / "dev.py").unlink()
    decision = _decide(_event("pre", main, "edit", [
        _edit_target(worktree, ".tackbox/approvals", [MANIFEST_ENTRY]),
    ]))
    assert decision["decision"] == "ask"
    (worktree / "dev.py").unlink()
    (main / "dev.py").write_text("# active main\n")
    assert _decide(_event("pre", main, "edit", [
        _edit_target(worktree, ".tackbox/approvals", [MANIFEST_ENTRY]),
    ]))["decision"] == "allow"


def test_nested_worktree_targets_do_not_inherit_parent_policy(tmp_path):
    main = _child_repo(tmp_path, "main")
    worktree = main / ".worktrees" / "linked"
    git(main, "worktree", "add", "-q", "-b", "linked", str(worktree))
    (main / ".gitattributes").write_text(".worktrees/** linguist-generated\n")
    decision = _decide(_event("pre", main, "edit", [
        _edit_target(worktree, "plain.py", ["x = 1"]),
    ]))
    assert decision["decision"] == "allow"


def test_symlinked_repository_path_enforces_resolved_target_policy(tmp_path):
    target_root = _child_repo(tmp_path, "target")
    alias = tmp_path / "alias"
    alias.symlink_to(target_root, target_is_directory=True)
    decision = _decide(_event("pre", tmp_path, "edit", [
        _edit_target(alias, ".tackbox/approvals", [MANIFEST_ENTRY]),
    ]))
    assert decision["decision"] == "ask"
    assert MANIFEST_ENTRY in decision["reason"]


def test_new_nested_attribute_carrier_is_gated_without_existing_parents(tmp_path):
    root = _child_repo(tmp_path, "target")
    line = "*.py linguist-generated"
    decision = _decide(_event("pre", tmp_path, "write", [
        _write_target(root, "new/deep/.gitattributes", line + "\n"),
    ]))
    assert decision["decision"] == "ask"
    assert line in decision["reason"]


def test_non_directory_target_parent_blocks_instead_of_becoming_inactive(tmp_path):
    root = _child_repo(tmp_path, "target")
    (root / "file").write_text("not a directory\n")
    decision = _decide(_event("pre", tmp_path, "write", [
        _write_target(root, "file/.tackbox/approvals", MANIFEST_ENTRY + "\n"),
    ]))
    assert decision["decision"] == "block"
    assert str(root / "file") in decision["reason"]


@pytest.mark.parametrize("phase", ["pre", "post"])
def test_unclassifiable_explicit_mutation_fails_closed_outside_git(tmp_path, phase):
    decision = _decide(_event(phase, tmp_path, "edit", [], unknown="no target path"))
    assert decision["decision"] == ("block" if phase == "pre" else "warn")
    assert "no target path" in decision["reason"]


def test_cross_repository_move_gates_source_and_destination_and_reports_landed_debt(tmp_path):
    source = _child_repo(tmp_path, "source")
    destination = _child_repo(tmp_path, "destination")
    targets = [
        {"path": str(source / "dev.py"), "op": "move", "expectedPresent": False, "moveId": "cross"},
        {"path": str(destination / ".tackbox/approvals"), "op": "move", "expectedPresent": True,
         "moveId": "cross", "ambiguous": True},
    ]
    pre = _decide(_event("pre", tmp_path, "edit", targets))
    assert pre["decision"] == "ask"
    for value in (str(source), str(destination), "root dev.py", ".tackbox/approvals"):
        assert value in pre["reason"]
    (source / "dev.py").unlink()
    (destination / ".tackbox").mkdir()
    (destination / ".tackbox/approvals").write_text(MANIFEST_ENTRY + "\n")
    post = _decide(_event("post", tmp_path, "edit", targets))
    assert post["decision"] == "block"
    assert "root dev.py was removed or moved" in post["reason"]
    assert "approval has no matching marker" in post["reason"]


def test_cross_repository_post_checks_both_sides_of_marker_move(tmp_path):
    source = _child_repo(tmp_path, "source")
    destination = _child_repo(tmp_path, "destination")
    marker = "# no-report: caller tolerates this failure\nx = 1\n"
    (source / "marker.py").write_text(marker)
    (source / ".tackbox").mkdir()
    (source / ".tackbox/approvals").write_text("marker.py: no-report: caller tolerates this failure\n")
    commit_all(source)
    (source / "marker.py").unlink()
    (destination / "marker.py").write_text(marker)
    decision = _decide(_event("post", tmp_path, "edit", [
        {"path": str(source / "marker.py"), "op": "move", "expectedPresent": False, "moveId": "marker"},
        {"path": str(destination / "marker.py"), "op": "move", "expectedPresent": True,
         "moveId": "marker", "content": marker},
    ]))
    assert decision["decision"] == "block"
    assert str(source) in decision["reason"] and str(destination) in decision["reason"]
    assert "approval has no matching marker" in decision["reason"]
    assert "unapproved no-report marker" in decision["reason"]


def test_post_uses_only_actual_target_repository_despite_launch_debt(tmp_path):
    launch = _child_repo(tmp_path, "launch")
    target_root = _child_repo(tmp_path, "target")
    (launch / "debt.py").write_text("# no-report: unrelated launch debt\nx = 1\n")
    (target_root / "clean.py").write_text("x = 1\n")
    decision = _decide(_event("post", launch, "edit", [
        _edit_target(target_root, "clean.py", ["x = 1"]),
    ], succeeded=False))
    assert decision == {"protocol": 1, "decision": "allow", "reason": ""}


def test_claude_relative_path_is_resolved_from_launch_cwd_to_target_repository(tmp_path):
    root = _child_repo(tmp_path, "target")
    event = {
        "hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(tmp_path),
        "tool_input": {"file_path": "target/.tackbox/approvals", "content": MANIFEST_ENTRY + "\n"},
    }
    result = subprocess.run(
        [sys.executable, "-m", "tackbox.cli", "hook"], input=json.dumps(event),
        cwd=TACKBOX_ROOT, env=tackbox_env(), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    decision = json.loads(result.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert MANIFEST_ENTRY in decision["permissionDecisionReason"]



@pytest.mark.parametrize("phase", ["pre", "post"])
def test_target_parent_permission_failure_is_unverified(tmp_path, monkeypatch, phase):
    root = _child_repo(tmp_path, "target")
    parent = root / "private"
    parent.mkdir()
    real_stat = Path.stat

    def inaccessible(path, *args, **kwargs):
        if path == parent:
            raise PermissionError(13, "permission denied", str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", inaccessible)
    event = hookproto.parse_request(_event(phase, tmp_path, "write", [
        _write_target(root, "private/.gitattributes", "*.py linguist-generated\n"),
    ]))
    outcome = cli._hook_outcome(event)
    decision = hookproto.wire_decision(outcome, phase)
    assert decision.decision == ("block" if phase == "pre" else "warn")
    assert str(parent) in decision.reason and "permission denied" in decision.reason


@pytest.mark.parametrize("phase", ["pre", "post"])
def test_explicit_target_with_missing_git_is_unverified_outside_repository(tmp_path, monkeypatch, phase):
    root = _child_repo(tmp_path, "target")

    def unavailable(*args, **kwargs):
        raise FileNotFoundError(2, "executable unavailable", "git")

    monkeypatch.setattr(cli.proc, "run", unavailable)
    event = hookproto.parse_request(_event(phase, tmp_path, "edit", [
        _edit_target(root, ".tackbox/approvals", [MANIFEST_ENTRY]),
    ]))
    decision = hookproto.wire_decision(cli._hook_outcome(event), phase)
    assert decision.decision == ("block" if phase == "pre" else "warn")
    assert "executable unavailable" in decision.reason


def test_pre_plain_delete_outside_launch_repository_preserves_free_removal(tmp_path):
    root = _child_repo(tmp_path, "target")
    decision = _decide(_event("pre", tmp_path, "edit", [{
        "path": str(root / "nested/gone.py"), "op": "delete", "expectedPresent": False,
    }]))
    assert decision == {"protocol": 1, "decision": "allow", "reason": ""}


def test_target_in_inactive_repository_does_not_inherit_launch_gate(tmp_path):
    launch = _child_repo(tmp_path, "launch")
    inactive = tmp_path / "inactive"
    inactive.mkdir()
    init_repo(inactive)
    decision = _decide(_event("pre", launch, "write", [
        _write_target(inactive, ".tackbox/approvals", MANIFEST_ENTRY + "\n"),
    ]))
    assert decision == {"protocol": 1, "decision": "allow", "reason": ""}



def test_cross_repository_attribute_policies_are_not_shared(tmp_path):
    launch = _child_repo(tmp_path, "launch")
    target_root = _child_repo(tmp_path, "target")
    (launch / ".gitattributes").write_text("plain.py linguist-generated\n")
    plain = _edit_target(target_root, "plain.py", ["x = 1"])
    assert _decide(_event("pre", launch, "edit", [plain]))["decision"] == "allow"
    (target_root / ".gitattributes").write_text("plain.py linguist-vendored\n")
    decision = _decide(_event("pre", launch, "edit", [plain]))
    assert decision["decision"] == "ask"
    assert "linguist-vendored" in decision["reason"]
    assert "linguist-generated" not in decision["reason"]


def test_wrong_launch_repository_does_not_hide_target_session_debt(tmp_path):
    launch = _child_repo(tmp_path, "launch")
    target_root = _child_repo(tmp_path, "target")
    (target_root / "debt.py").write_text("# no-report: caller tolerates this failure\nx = 1\n")
    decision = _decide(_event("pre", launch, "edit", [
        _edit_target(target_root, "other.py", ["x = 1"]),
    ]))
    assert decision["decision"] == "block"
    assert "debt.py:1:" in decision["reason"]


def test_deleted_target_with_absent_parent_directory_still_reports_approval_debt(tmp_path):
    root = _child_repo(tmp_path, "target")
    nested = root / "nested"
    nested.mkdir()
    (nested / "marker.py").write_text("# no-report: caller tolerates this failure\nx = 1\n")
    (root / ".tackbox").mkdir()
    (root / ".tackbox/approvals").write_text("nested/marker.py: no-report: caller tolerates this failure\n")
    commit_all(root)
    (nested / "marker.py").unlink()
    nested.rmdir()
    decision = _decide(_event("post", tmp_path, "edit", [{
        "path": str(nested / "marker.py"), "op": "delete", "expectedPresent": False,
    }]))
    assert decision["decision"] == "block"
    assert "approval has no matching marker" in decision["reason"]



def test_multi_repository_debt_refusal_preserves_other_repository_approval_reason(tmp_path):
    indebted = _child_repo(tmp_path, "indebted")
    gated = _child_repo(tmp_path, "gated")
    (indebted / "debt.py").write_text("# no-report: caller tolerates this failure\nx = 1\n")
    decision = _decide(_event("pre", tmp_path, "edit", [
        _edit_target(indebted, "unrelated.py", ["x = 1"]),
        _edit_target(gated, ".tackbox/approvals", [MANIFEST_ENTRY]),
    ]))
    assert decision["decision"] == "block"
    for value in (str(indebted), str(gated), "debt.py:1:", MANIFEST_ENTRY):
        assert value in decision["reason"]


def test_multi_repository_debt_refusal_reports_every_blocked_worktree(tmp_path):
    first = _child_repo(tmp_path, "first")
    second = _child_repo(tmp_path, "second")
    for root in (first, second):
        (root / "debt.py").write_text("# no-report: caller tolerates this failure\nx = 1\n")
    decision = _decide(_event("pre", tmp_path, "edit", [
        _edit_target(first, "unrelated.py", ["x = 1"]),
        _edit_target(second, "unrelated.py", ["x = 1"]),
    ]))
    assert decision["decision"] == "block"
    assert str(first) in decision["reason"] and str(second) in decision["reason"]
    assert decision["reason"].count("debt.py:1:") == 2



@pytest.mark.parametrize("gate", [".tackbox/approvals", ".gitattributes"])
def test_file_symlink_alias_uses_target_gate_name(tmp_path, gate):
    root = _child_repo(tmp_path, "target")
    path = root / gate
    path.parent.mkdir(exist_ok=True)
    path.write_text("")
    alias = tmp_path / "alias.txt"
    alias.symlink_to(path)
    text = MANIFEST_ENTRY if gate == ".tackbox/approvals" else "*.py linguist-generated"
    decision = _decide(_event("pre", tmp_path, "edit", [
        _edit_target(tmp_path, "alias.txt", [text]),
    ]))
    assert decision["decision"] == "ask"
    assert text in decision["reason"]



@pytest.mark.parametrize("repair", ["marker-file", "manifest"])
def test_indebted_repository_repair_and_clean_repository_edit_remain_independent(tmp_path, repair):
    indebted = _child_repo(tmp_path, "indebted")
    clean = _child_repo(tmp_path, "clean")
    marker = "# no-report: caller tolerates this failure\nx = 1\n"
    (indebted / "marker.py").write_text(marker)
    clean_target = _edit_target(clean, "plain.py", ["x = 2"])
    repair_target = (
        _edit_target(indebted, "marker.py", ["x = 2"], removed=["x = 1"])
        if repair == "marker-file" else
        _edit_target(indebted, ".tackbox/approvals", ["marker.py: no-report: caller tolerates this failure"])
    )
    mixed = _decide(_event("pre", tmp_path, "edit", [repair_target, clean_target]))
    assert mixed["decision"] == ("allow" if repair == "marker-file" else "ask")
    if repair == "manifest":
        assert "marker.py: no-report: caller tolerates this failure" in mixed["reason"]
    independent = _decide(_event("pre", indebted, "edit", [clean_target]))
    assert independent["decision"] == "allow"
    unrelated = _edit_target(indebted, "unrelated.py", ["x = 1"])
    blocked = _decide(_event("pre", tmp_path, "edit", [repair_target, clean_target, unrelated]))
    assert blocked["decision"] == "block"
    assert "marker.py:1:" in blocked["reason"]


def test_committed_marker_and_orphan_debt_does_not_block_general_hook_work(tmp_path):
    root = _child_repo(tmp_path, "target")
    (root / "unapproved.py").write_text("# no-report: caller tolerates this failure\nx = 1\n")
    (root / "approved.py").write_text("# no-report: caller tolerates this failure\nx = 1\n")
    (root / ".tackbox").mkdir()
    manifest = root / ".tackbox/approvals"
    manifest.write_text("approved.py: no-report: caller tolerates this failure\nmissing.py: no-report: committed orphan belongs to CI\n")
    commit_all(root)
    general = _edit_target(root, "plain.py", ["x = 1"])
    assert _decide(_event("pre", tmp_path, "edit", [general]))["decision"] == "allow"
    (root / "unapproved.py").write_text("# no-report: caller tolerates this failure\nx = 2\n")
    assert _decide(_event("pre", tmp_path, "edit", [general]))["decision"] == "allow"
    manifest.write_text(manifest.read_text() + "new-missing.py: no-report: added orphan blocks general work\n")
    assert _decide(_event("pre", tmp_path, "edit", [general]))["decision"] == "block"


def test_dirty_debt_before_first_event_blocks_until_marker_and_entry_are_consistent(tmp_path):
    root = _child_repo(tmp_path, "target")
    (root / "marker.py").write_text("# no-report: caller tolerates this failure\nx = 1\n")
    general = _edit_target(root, "plain.py", ["x = 1"])
    assert _decide(_event("pre", tmp_path, "edit", [general]))["decision"] == "block"
    (root / ".tackbox").mkdir()
    (root / ".tackbox/approvals").write_text("marker.py: no-report: caller tolerates this failure\n")
    assert _decide(_event("pre", tmp_path, "edit", [general]))["decision"] == "allow"


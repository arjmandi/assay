"""The Claude Code hooks (docs/ARCHITECTURE.md section 8.4): the PreToolUse
script driven as a subprocess on synthetic hook input, and the two commands
driven through the real CLI. The hook refuses a write under `.assay/` or
`.claude/`, under the anchor directory, and to the token or policy file;
allows the one agent-owned file, `.assay/NOTES.md`; refuses a Bash command
naming a refused path unless it is a single simple command by the pinned
launcher (or by `"$ASSAY"` when the policy pins the exported value), a
compound command whatever it begins with, a change to the shell state the
launcher's trust rests on, and a command matching a deny pattern; and allows
everything else with no output. `hooks install` writes the policy (0600) and
the exact hook entries, merging into an existing settings file without
clobbering a foreign entry, idempotent, and refuses a policy path inside the
run directory. `hooks post-tool-use` appends the fixed-shape `tool_use`
record with the receipt's `end_event` from a prose receipt, from a `--json`
receipt document and from an mcp tool's record, null for a plain command."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ASSAY_CLI

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "pre_tool_use.py"
LAUNCHER = REPO / "bin" / "assay"
MATCHERS = ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit")
ELSEWHERE = "your files go in the run directory outside .assay and .claude"


def _cli(run: Path, *args: str, env: dict[str, str] | None = None, stdin: str | None = None):
    """The real CLI against a run directory, with the environment and the
    stdin a hook command gets."""
    environment = dict(os.environ)
    environment.pop("ASSAY", None)
    if env:
        environment.update(env)
    return subprocess.run(
        [sys.executable, str(ASSAY_CLI), "--run-dir", str(run), *args],
        capture_output=True,
        text=True,
        timeout=180,
        env=environment,
        input=stdin,
    )


def _install(tmp_path: Path, run: Path, *extra: str, env: dict[str, str] | None = None):
    """`hooks install` for a run directory, the policy beside the run."""
    run.mkdir(exist_ok=True)
    policy = tmp_path / "operator" / "policy.json"
    result = _cli(run, "hooks", "install", "--policy", str(policy), *extra, env=env)
    assert result.returncode == 0, result.stderr
    return result, policy


def _hook(policy: Path, payload: dict, *, cwd: Path, project_dir: Path | None):
    """The PreToolUse script on one hook event, in Claude Code's position:
    CLAUDE_PROJECT_DIR set (or not), `ASSAY` not exported."""
    environment = dict(os.environ)
    environment.pop("ASSAY", None)
    environment.pop("CLAUDE_PROJECT_DIR", None)
    if project_dir is not None:
        environment["CLAUDE_PROJECT_DIR"] = str(project_dir)
    return subprocess.run(
        [sys.executable, str(HOOK), "--policy", str(policy)],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
        input=json.dumps({"session_id": "s1", "cwd": str(cwd), "hook_event_name": "PreToolUse", **payload}),
    )


def _write(path: str, tool: str = "Write") -> dict:
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"tool_name": tool, "tool_input": {key: path, "content": "x"}, "tool_use_id": "toolu_w"}


def _bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "tool_use_id": "toolu_b"}


def _refused(result, line: str) -> None:
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == ""
    assert result.stderr == f"HOOK | REFUSED | {line}\n"


def _allowed(result) -> None:
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == "" and result.stderr == ""


def _anchor_dir() -> Path:
    return Path(os.environ["ASSAY_ANCHOR_DIR"]).resolve()


# --- the PreToolUse script: writes ---------------------------------------------


def test_a_write_under_the_state_directory_or_the_settings_is_refused(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        _refused(
            _hook(policy, _write(".assay/events.jsonl", tool), cwd=run, project_dir=run),
            ".assay/events.jsonl lies under .assay, which the daemon writes; .assay/NOTES.md is "
            "the one file there that is yours, the rest is reached through assay",
        )
    _refused(
        _hook(policy, _write(str(run / ".assay" / "verifiers" / "v.py")), cwd=run, project_dir=run),
        f"{run / '.assay' / 'verifiers' / 'v.py'} lies under .assay, which the daemon writes; "
        ".assay/NOTES.md is the one file there that is yours, the rest is reached through assay",
    )
    _refused(
        _hook(policy, _write(".claude/settings.json"), cwd=run, project_dir=run),
        f".claude/settings.json lies under .claude, the operator's settings; nothing there is yours to write, {ELSEWHERE}",
    )
    # A symlink into the state directory resolves to it.
    (run / ".assay").mkdir()
    (run / "link").symlink_to(run / ".assay")
    _refused(
        _hook(policy, _write("link/events.jsonl"), cwd=run, project_dir=run),
        "link/events.jsonl lies under .assay, which the daemon writes; .assay/NOTES.md is the "
        "one file there that is yours, the rest is reached through assay",
    )


def test_a_write_under_the_anchor_directory_or_to_the_operators_files_is_refused(tmp_path):
    run = tmp_path / "run"
    token = tmp_path / "operator" / "owner.token"
    _, policy = _install(tmp_path, run, "--owner-token-file", str(token))
    anchored = _anchor_dir() / "abc.jsonl"
    _refused(
        _hook(policy, _write(str(anchored)), cwd=run, project_dir=run),
        f"{anchored} lies under the anchor directory, the operator's record of the chain heads; "
        f"nothing there is yours to write, {ELSEWHERE}",
    )
    _refused(
        _hook(policy, _write(str(token)), cwd=run, project_dir=run),
        f"{token.resolve()} is the owner token file, which the operator holds; nothing there is yours to write, {ELSEWHERE}",
    )
    _refused(
        _hook(policy, _write(str(policy)), cwd=run, project_dir=run),
        f"{policy.resolve()} is the hook policy file, which the operator holds; nothing there is yours to write, {ELSEWHERE}",
    )


def test_an_edit_of_the_notes_is_allowed_and_paths_resolve_against_the_project_directory(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    # Relative to CLAUDE_PROJECT_DIR, then to the hook's cwd, then realpath.
    _allowed(_hook(policy, _write(".assay/NOTES.md", "Edit"), cwd=tmp_path, project_dir=run))
    _allowed(_hook(policy, _write(".assay/NOTES.md", "Edit"), cwd=run, project_dir=None))
    _allowed(_hook(policy, _write(str(run / "sub" / ".." / ".assay" / "NOTES.md"), "Write"), cwd=run, project_dir=run))
    _refused(
        _hook(policy, _write(".assay/events.jsonl", "Edit"), cwd=run, project_dir=None),
        ".assay/events.jsonl lies under .assay, which the daemon writes; .assay/NOTES.md is the "
        "one file there that is yours, the rest is reached through assay",
    )
    # The agent's own files in the run directory, and another directory's
    # state, which this run's policy does not cover.
    _allowed(_hook(policy, _write("checks/first.py"), cwd=run, project_dir=run))
    _allowed(_hook(policy, _write(str(tmp_path / "other" / ".assay" / "x")), cwd=run, project_dir=run))


# --- the PreToolUse script: Bash --------------------------------------------------


def test_rm_rf_and_ln_s_naming_the_state_directory_are_refused(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    single = f"the allowed form is a single {LAUNCHER} command, e.g. {LAUNCHER} commit @.assay/model_plan.json"
    for command, first in (
        ("rm -rf .assay", "rm"),
        ("ln -s .assay x", "ln"),
        ("rm -rf ./.assay/", "rm"),
        ('rm -rf "$PWD/.assay"', "rm"),
        ("rm -rf '.assay'", "rm"),
        ("rm -rf .a*", "rm"),
        ("cat .assay/events.jsonl", "cat"),
        ("cp x .claude/settings.json", "cp"),
        ("assay commit @.assay/model_plan.json", "assay"),
    ):
        what = ".claude (the operator's settings)" if ".claude" in command else ".assay (the run's state directory)"
        _refused(
            _hook(policy, _bash(command), cwd=run, project_dir=run),
            f"the command names {what} and begins with {first!r}, not the launcher; {single}",
        )
    # The anchor directory and the token file by their paths, relative to
    # the cwd and absolute.
    anchor = _anchor_dir()
    _refused(
        _hook(policy, _bash(f"rm -rf {anchor.name}"), cwd=anchor.parent, project_dir=run),
        f"the command names the anchor directory and begins with 'rm', not the launcher; {single}",
    )
    _refused(
        _hook(policy, _bash(f"truncate -s 0 {anchor / 'x.jsonl'}"), cwd=run, project_dir=run),
        f"the command names the anchor directory and begins with 'truncate', not the launcher; {single}",
    )
    _refused(
        _hook(policy, _bash(f"cat {policy}"), cwd=run, project_dir=run),
        f"the command names the hook policy file and begins with 'cat', not the launcher; {single}",
    )


def test_a_compound_command_naming_the_state_directory_is_refused_whatever_it_begins_with(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    compound = (
        "the command names .assay (the run's state directory) inside a compound command (;, &&, "
        f"||, |, a newline, $( or a backtick); the allowed form is a single {LAUNCHER} command, "
        f"e.g. {LAUNCHER} commit @.assay/model_plan.json"
    )
    for command in (
        f"{LAUNCHER} status; rm -rf .assay",
        f"{LAUNCHER} status && cat .assay/events.jsonl",
        f"{LAUNCHER} status || rm -rf .assay",
        f"{LAUNCHER} commit @.assay/model_plan.json | tee out.txt",
        f"{LAUNCHER} status\nrm -rf .assay",
        f"{LAUNCHER} view --event $(cat .assay/x)",
        f"{LAUNCHER} view --event `cat .assay/x`",
        "cd .assay; ls",
    ):
        _refused(_hook(policy, _bash(command), cwd=run, project_dir=run), compound)
    _refused(
        _hook(policy, _bash(f"{LAUNCHER} status > .assay/events.jsonl"), cwd=run, project_dir=run),
        "the command names .assay (the run's state directory) beside a redirection; the allowed "
        f"form is a single {LAUNCHER} command, e.g. {LAUNCHER} commit @.assay/model_plan.json, "
        "with no redirection",
    )
    _refused(
        _hook(policy, _bash(f"{LAUNCHER} commit @.assay/model_plan.json 'unterminated"), cwd=run, project_dir=run),
        "the command names .assay (the run's state directory) and does not parse as one shell "
        f"command (No closing quotation); the allowed form is a single {LAUNCHER} command, e.g. "
        f"{LAUNCHER} commit @.assay/model_plan.json",
    )


def test_a_single_launcher_command_naming_the_state_directory_is_allowed(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    _allowed(_hook(policy, _bash(f"{LAUNCHER} commit @.assay/model_plan.json"), cwd=run, project_dir=run))
    _allowed(_hook(policy, _bash(f'{LAUNCHER} act RUN --params @.assay/params.json --predict "change"'), cwd=run, project_dir=run))
    # The variable is not trusted on its own: the policy must pin the value
    # the operator exported before the session.
    _refused(
        _hook(policy, _bash('"$ASSAY" commit @.assay/model_plan.json'), cwd=run, project_dir=run),
        "the command names .assay (the run's state directory) and begins with '$ASSAY', not the "
        f"launcher; the allowed form is a single {LAUNCHER} command, e.g. {LAUNCHER} commit "
        "@.assay/model_plan.json",
    )
    pinned_run = tmp_path / "pinned"
    _, pinned = _install(tmp_path / "second", pinned_run, env={"ASSAY": str(LAUNCHER)})
    assert json.loads(pinned.read_text())["assay_value"] == str(LAUNCHER)
    _allowed(_hook(pinned, _bash('"$ASSAY" commit @.assay/model_plan.json'), cwd=pinned_run, project_dir=pinned_run))
    _allowed(_hook(pinned, _bash("$ASSAY commit @.assay/model_plan.json"), cwd=pinned_run, project_dir=pinned_run))
    _refused(
        _hook(pinned, _bash("assay commit @.assay/model_plan.json"), cwd=pinned_run, project_dir=pinned_run),
        "the command names .assay (the run's state directory) and begins with 'assay', not the "
        f'launcher; the allowed form is a single {LAUNCHER} (or "$ASSAY", the launcher the '
        f"operator exported) command, e.g. {LAUNCHER} commit @.assay/model_plan.json",
    )


def test_a_change_to_the_shell_state_is_refused(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    line = (
        "the command assigns, exports, unsets or aliases ASSAY or PATH, or defines a function "
        f"named assay; the launcher is {LAUNCHER}, run it by that path and leave the shell as it is"
    )
    for command in (
        "export ASSAY=/tmp/x",
        "ASSAY=/tmp/x",
        "PATH=/tmp:$PATH",
        "export PATH",
        "unset ASSAY",
        "alias assay=/tmp/x",
        "assay() { /tmp/x \"$@\"; }",
        "function assay { :; }",
        "declare -x ASSAY=/tmp/x",
        "env ASSAY=/tmp/x /tmp/y status",
        "echo ${ASSAY:=/tmp/x}",
        "read -r ASSAY",
    ):
        _refused(_hook(policy, _bash(command), cwd=run, project_dir=run), line)
    _allowed(_hook(policy, _bash("PYTHONPATH=src python3 -c 'print(1)'"), cwd=run, project_dir=run))
    _allowed(_hook(policy, _bash('echo "$ASSAY" "$PATH"'), cwd=run, project_dir=run))


def test_a_direct_world_call_is_refused_by_a_deny_pattern(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run, "--deny", "python3? .*world_client", "--deny", "curl .*world[.]example")
    assert json.loads(policy.read_text())["deny"] == ["python3? .*world_client", "curl .*world[.]example"]
    _refused(
        _hook(policy, _bash("python3 -c 'import world_client; world_client.step(1)'"), cwd=run, project_dir=run),
        "the command matches the operator's deny pattern 'python3? .*world_client': the world is "
        f"reached only through the harness; the allowed form is {LAUNCHER} act NAME pname=value "
        '--predict "..."',
    )
    _refused(
        _hook(policy, _bash("curl -X POST https://world.example/step"), cwd=run, project_dir=run),
        "the command matches the operator's deny pattern 'curl .*world[.]example': the world is "
        f"reached only through the harness; the allowed form is {LAUNCHER} act NAME pname=value "
        '--predict "..."',
    )
    _allowed(_hook(policy, _bash("python3 -c 'print(1)'"), cwd=run, project_dir=run))
    refused = _cli(run, "hooks", "install", "--policy", str(tmp_path / "p2.json"), "--deny", "(unclosed")
    assert refused.returncode == 2
    assert refused.stderr.startswith("ERROR | COMMAND_ARGS | --deny '(unclosed' does not compile: ")


def test_a_command_outside_every_rule_is_allowed_with_no_output(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    for command in (
        "ls -la",
        "git status",
        f"{LAUNCHER} status",
        "assay status",
        '"$ASSAY" act MOVE direction=north --predict "change" --because "map the verb"',
        "cat checks/first.py | head",
        "python3 -c 'print(1)' > out.txt",
    ):
        _allowed(_hook(policy, _bash(command), cwd=run, project_dir=run))
    # A tool the matchers do not name, and an event without a command.
    _allowed(_hook(policy, {"tool_name": "Read", "tool_input": {"file_path": ".assay/events.jsonl"}, "tool_use_id": "t"}, cwd=run, project_dir=run))
    _allowed(_hook(policy, {"tool_name": "Bash", "tool_input": {}, "tool_use_id": "t"}, cwd=run, project_dir=run))


def test_the_script_fails_closed_without_its_policy_or_its_input(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    missing = tmp_path / "gone.json"
    result = _hook(missing, _bash("ls"), cwd=run, project_dir=run)
    assert result.returncode == 2 and result.stdout == ""
    assert result.stderr.startswith(
        f"HOOK | REFUSED | the hook policy file {missing} cannot be read (FileNotFoundError: "
    )
    assert result.stderr.rstrip("\n").endswith(
        f"; the operator reinstalls the hooks with `assay hooks install --policy {missing}`"
    )
    (tmp_path / "shape.json").write_text(json.dumps({"version": 2}))
    result = _hook(tmp_path / "shape.json", _bash("ls"), cwd=run, project_dir=run)
    assert result.returncode == 2
    assert result.stderr == (
        f"HOOK | REFUSED | the hook policy file {tmp_path / 'shape.json'} is not a policy file "
        "(version 2 is not 1); the operator reinstalls the hooks with `assay hooks install "
        f"--policy {tmp_path / 'shape.json'}`\n"
    )
    result = subprocess.run(
        [sys.executable, str(HOOK), "--policy", str(policy)],
        capture_output=True, text=True, timeout=60, input="not json",
    )
    assert result.returncode == 2 and result.stdout == ""
    assert result.stderr.startswith("HOOK | REFUSED | the hook input is not JSON (")


# --- hooks install ------------------------------------------------------------------


def test_hooks_install_writes_the_settings_and_the_policy(tmp_path):
    run = tmp_path / "run"
    token = tmp_path / "operator" / "owner.token"
    result, policy = _install(tmp_path, run, "--deny", "world_client", "--owner-token-file", str(token))
    settings = run / ".claude" / "settings.json"
    assert result.stdout == f"HOOKS | installed 11 entries in {settings}; policy {policy}\n"
    assert stat.S_IMODE(policy.stat().st_mode) == 0o600
    python = str(Path(sys.executable).resolve())
    assert json.loads(policy.read_text()) == {
        "version": 1,
        "run_dir": str(run.resolve()),
        "anchor_dir": str(_anchor_dir()),
        "token_file": str(token.resolve()),
        "policy_file": str(policy.resolve()),
        "launcher": str(LAUNCHER),
        "assay_value": None,
        "deny": ["world_client"],
        "python": python,
    }
    pre = f"{python} {HOOK} --policy {policy}"
    post = f"{LAUNCHER} hooks post-tool-use --policy {policy}"
    assert json.loads(settings.read_text()) == {
        "hooks": {
            "PreToolUse": [
                {"matcher": matcher, "hooks": [{"type": "command", "command": pre}]} for matcher in MATCHERS
            ],
            "PostToolUse": [
                {"matcher": matcher, "hooks": [{"type": "command", "command": post}]}
                for matcher in (*MATCHERS, "mcp__assay__.*")
            ],
        }
    }
    machine = _cli(run, "hooks", "install", "--policy", str(policy), "--json")
    assert machine.returncode == 0, machine.stderr
    assert json.loads(machine.stdout) == {"lines": [f"HOOKS | installed 11 entries in {settings}; policy {policy}"]}


def test_hooks_install_merges_without_clobbering_a_foreign_entry_and_is_idempotent(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    settings = run / ".claude" / "settings.json"
    settings.parent.mkdir()
    foreign = {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/local/bin/lint.sh"}]}
    settings.write_text(json.dumps({"permissions": {"deny": ["Bash(sudo:*)"]}, "hooks": {"PreToolUse": [foreign]}}))
    _, policy = _install(tmp_path, run)
    first = settings.read_text()
    written = json.loads(first)
    assert written["permissions"] == {"deny": ["Bash(sudo:*)"]}
    assert written["hooks"]["PreToolUse"][0] == foreign
    assert len(written["hooks"]["PreToolUse"]) == 6 and len(written["hooks"]["PostToolUse"]) == 6
    policy_text = policy.read_text()
    _install(tmp_path, run)
    assert settings.read_text() == first
    assert policy.read_text() == policy_text
    # Another policy path replaces the harness's entries instead of adding to them.
    moved = tmp_path / "operator" / "policy2.json"
    again = _cli(run, "hooks", "install", "--policy", str(moved))
    assert again.returncode == 0, again.stderr
    rewritten = json.loads(settings.read_text())
    assert rewritten["hooks"]["PreToolUse"][0] == foreign
    assert len(rewritten["hooks"]["PreToolUse"]) == 6 and len(rewritten["hooks"]["PostToolUse"]) == 6
    commands = [item["command"] for entry in rewritten["hooks"]["PreToolUse"][1:] for item in entry["hooks"]]
    assert commands == [f"{Path(sys.executable).resolve()} {HOOK} --policy {moved}"] * 5


def test_hooks_install_refuses_a_policy_path_inside_the_run_directory_and_pins_the_launcher(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    inside = run / "policy.json"
    refused = _cli(run, "hooks", "install", "--policy", str(inside))
    assert refused.returncode == 2
    assert refused.stderr == f"ERROR | PATH_INVALID | --policy must point outside the run directory, got {inside.resolve()}\n"
    assert not inside.exists() and not (run / ".claude").exists()
    # The launcher: pinned explicitly, outside the run directory, executable.
    launcher = tmp_path / "bin" / "assay"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o755)
    _, policy = _install(tmp_path, run, "--launcher", str(launcher))
    assert json.loads(policy.read_text())["launcher"] == str(launcher.resolve())
    post = json.loads((run / ".claude" / "settings.json").read_text())["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert post == f"{launcher.resolve()} hooks post-tool-use --policy {policy}"
    refused = _cli(run, "hooks", "install", "--policy", str(policy), "--launcher", str(run / "assay"))
    assert refused.returncode == 2
    assert refused.stderr == f"ERROR | PATH_INVALID | --launcher must point outside the run directory, got {run.resolve() / 'assay'}\n"
    refused = _cli(run, "hooks", "install", "--policy", str(policy), env={"ASSAY": "/tmp/elsewhere/assay"})
    assert refused.returncode == 2
    assert refused.stderr.startswith(
        f"ERROR | COMMAND_ARGS | ASSAY is exported as /tmp/elsewhere/assay and the launcher is {LAUNCHER}; "
    )


# --- hooks post-tool-use ---------------------------------------------------------------


def _run_state(run: Path) -> None:
    (run / ".assay").mkdir(parents=True)
    (run / ".assay" / "config.json").write_text(json.dumps({"game_id": "w1", "mode": "local"}))


def _post(policy: Path, payload: dict, run: Path):
    return _cli(
        run,
        "hooks",
        "post-tool-use",
        "--policy",
        str(policy),
        stdin=json.dumps({"session_id": "s1", "cwd": str(run), "hook_event_name": "PostToolUse", **payload}),
    )


def _records(run: Path) -> list[dict]:
    return [json.loads(line) for line in (run / ".assay" / "activity.jsonl").read_text().splitlines() if line]


PROSE_RECEIPT = (
    "OUTCOME | PREDICTED | all 1 claims held\n"
    "  ✓ change\n"
    "EVENT | e3 | progress 1/1 | paid actions 3 | NOT_FINISHED\n"
    "KEY DELTA | last step (before -> after)\n"
    "  counter: 2 -> 3\n"
)


def test_the_post_tool_use_record_joins_by_end_event(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    _run_state(run)
    json_receipt = json.dumps(
        {"kind": "act", "outcome": "PREDICTED", "detail": "all 1 claims held", "start_event": 4, "end_event": 5, "estimated_tokens": 40}
    )
    mcp_record = json.dumps({"kind": "act", "outcome": "SURPRISE", "start_event": 6, "end_event": 7, "text": PROSE_RECEIPT})
    long_command = "echo " + "x" * 100
    for payload in (
        {"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": f"{LAUNCHER} act INC amount=1 --predict change"},
         "tool_response": {"stdout": PROSE_RECEIPT, "stderr": "", "interrupted": False, "isImage": False}},
        {"tool_name": "Bash", "tool_use_id": "t2", "tool_input": {"command": f"{LAUNCHER} act INC amount=1 --predict change --json"},
         "tool_response": {"stdout": json_receipt + "\n", "stderr": "", "interrupted": False, "isImage": False}},
        {"tool_name": "Bash", "tool_use_id": "t3", "tool_input": {"command": "ls -la"},
         "tool_response": {"stdout": "total 0\n", "stderr": "", "interrupted": False, "isImage": False}},
        {"tool_name": "mcp__assay__act", "tool_use_id": "t4", "tool_input": {"action": "INC", "params": {"amount": 1}, "predict": "change"},
         "tool_response": [{"type": "text", "text": mcp_record}]},
        {"tool_name": "Edit", "tool_use_id": "t5", "tool_input": {"file_path": ".assay/NOTES.md", "old_string": "a", "new_string": "b"},
         "tool_response": {"filePath": ".assay/NOTES.md", "type": "update"}},
        {"tool_name": "Bash", "tool_use_id": "t6", "tool_input": {"command": long_command},
         "tool_response": {"stdout": "", "stderr": "", "interrupted": False, "isImage": False}},
    ):
        result = _post(policy, payload, run)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "" and result.stderr == ""
    records = _records(run)
    assert [sorted(record) for record in records] == [
        ["command_prefix", "end_event", "kind", "session_id", "timestamp", "tool", "tool_use_id"]
    ] * 6
    assert [(r["kind"], r["session_id"], r["tool_use_id"], r["tool"], r["end_event"]) for r in records] == [
        ("tool_use", "s1", "t1", "Bash", 3),
        ("tool_use", "s1", "t2", "Bash", 5),
        ("tool_use", "s1", "t3", "Bash", None),
        ("tool_use", "s1", "t4", "mcp__assay__act", 7),
        ("tool_use", "s1", "t5", "Edit", None),
        ("tool_use", "s1", "t6", "Bash", None),
    ]
    assert records[0]["command_prefix"] == f"{LAUNCHER} act INC amount=1 --predict change"[:80]
    assert records[2]["command_prefix"] == "ls -la"
    assert records[3]["command_prefix"] == "mcp__assay__act"
    assert records[4]["command_prefix"] == ".assay/NOTES.md"
    assert records[5]["command_prefix"] == long_command[:80] and len(records[5]["command_prefix"]) == 80
    machine = _post(policy, {"tool_name": "Bash", "tool_use_id": "t7", "tool_input": {"command": "ls"}, "tool_response": {"stdout": ""}}, run)
    assert machine.returncode == 0 and machine.stdout == ""
    quiet = _cli(run, "hooks", "post-tool-use", "--policy", str(policy), "--json",
                 stdin=json.dumps({"session_id": "s1", "tool_name": "Bash", "tool_use_id": "t8", "tool_input": {"command": "ls"}}))
    assert quiet.returncode == 0, quiet.stderr
    assert quiet.stdout == '{"lines":[]}\n'
    assert len(_records(run)) == 8


def test_the_post_tool_use_command_refuses_malformed_input_and_a_bad_policy(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    _run_state(run)
    result = _cli(run, "hooks", "post-tool-use", "--policy", str(policy), stdin="not json")
    assert result.returncode == 2 and result.stdout == ""
    assert result.stderr.startswith("ERROR | HOOK_INPUT_MALFORMED | the hook input is not JSON: ")
    result = _cli(run, "hooks", "post-tool-use", "--policy", str(policy), stdin=json.dumps({"session_id": "s1", "tool_name": "Bash"}))
    assert result.returncode == 2
    assert result.stderr.startswith("ERROR | HOOK_INPUT_MALFORMED | the hook input lacks tool_use_id\n")
    result = _cli(run, "hooks", "post-tool-use", "--policy", str(policy), stdin="")
    assert result.returncode == 2
    assert result.stderr.startswith("ERROR | HOOK_INPUT_MALFORMED | the hook input is not a JSON object\n")
    assert not (run / ".assay" / "activity.jsonl").exists()
    missing = tmp_path / "gone.json"
    result = _post(missing, {"tool_name": "Bash", "tool_use_id": "t", "tool_input": {"command": "ls"}}, run)
    assert result.returncode == 2
    assert result.stderr.startswith(f"ERROR | HOOK_POLICY_INVALID | the hook policy file {missing} cannot be read: FileNotFoundError: ")
    assert "NEXT | reinstall the hooks with `assay hooks install --policy FILE ...` from the operator's shell" in result.stderr
    machine = _cli(run, "hooks", "post-tool-use", "--policy", str(missing), "--json", stdin="{}")
    assert machine.returncode == 2
    assert json.loads(machine.stdout)["code"] == "HOOK_POLICY_INVALID"
    # No run in the policy's directory: nothing is written there.
    bare = tmp_path / "bare"
    _, bare_policy = _install(tmp_path / "bare-operator", bare)
    result = _post(bare_policy, {"tool_name": "Bash", "tool_use_id": "t", "tool_input": {"command": "ls"}}, bare)
    assert result.returncode == 2
    assert result.stderr.startswith(f"ERROR | RUN_MISSING | {bare.resolve()} is not initialized\n")
    assert not (bare / ".assay").exists()


def test_the_hook_script_imports_nothing_from_the_harness():
    import ast

    tree = ast.parse(HOOK.read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    assert modules and all(name.split(".")[0] in sys.stdlib_module_names for name in modules), modules
    assert not any(name.split(".")[0] in {"assay", "assay_grid", "numpy", "PIL"} for name in modules)


@pytest.mark.parametrize("command", ["hooks install", "hooks post-tool-use"])
def test_the_hook_commands_render_their_help(command, tmp_path):
    result = _cli(tmp_path, *command.split(), "--help")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: assay {command} [-h] --policy FILE")

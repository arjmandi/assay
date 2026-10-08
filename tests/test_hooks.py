"""The Claude Code hooks (docs/ARCHITECTURE.md section 8.4): the PreToolUse
script driven as a subprocess on synthetic hook input, and the two commands
driven through the real CLI. The hook refuses a write whose path has the
component `.assay`, `.claude` or `.claude.json` (case folded, wherever it
lies), under the anchor directory, to the token or policy file, or to the
installation's own paths, and allows the one agent-owned file,
`.assay/NOTES.md`; refuses a Bash command naming a refused path unless it is
a single simple command by the pinned launcher (or by `"$ASSAY"` when the
policy pins the exported value) with a sub-command other than `python` or
`export`, a compound command whatever it begins with, a change to the shell
state the launcher's trust rests on, the verbs that run text it cannot see
or remove by rule, a word spelled in bytes, the removal of the run
directory, and a command matching a deny pattern; and allows everything
else, the documented allowed forms with operators inside quotes among them,
with no output. `hooks install` writes the policy (0600) and the exact hook
entries, merging into an existing settings file without clobbering a foreign
entry, idempotent, refuses a policy path inside the run directory and a
symlinked `.claude`, and `--check` (and `assay doctor`) run the pinned
interpreter on the script. `hooks post-tool-use` appends the fixed-shape
`tool_use` record with the receipt's `end_event` from a prose receipt, from a
`--json` receipt document and from an mcp record, null for a plain command
and for a field nested elsewhere. The kernel's `export --out` refuses a
target under `.assay` or `.claude` whatever the hooks."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from conftest import ASSAY_CLI, run_of

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "pre_tool_use.py"
LAUNCHER = REPO / "bin" / "assay"
MATCHERS = ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit")
ELSEWHERE = "your files go in the run directory outside .assay and .claude"
STATE_TEXT = (
    "lies under .assay, which the daemon writes; .assay/NOTES.md is the one file there that "
    "is yours, the rest is reached through assay"
)
SHELL_STATE = (
    "the command changes the shell state the launcher's trust rests on (an assignment, export, "
    "unset or read of ASSAY*, PYTHON*, PATH, BASH_ENV, ENV, PROMPT_COMMAND, LD_PRELOAD or "
    f"DYLD_*, an alias of assay, or a function named assay or as the launcher); the launcher is "
    f"{LAUNCHER}, run it by that path and leave the shell as it is"
)
VERBS = (
    "the command runs eval, source (or . FILE), exec, xargs or trap, which run text the hook "
    "cannot see; write the command out plainly, one simple command at a time"
)
BY_RULE = (
    "the command removes by rule (find -delete or -exec, git clean, rsync --delete), which the "
    "hook cannot follow; name what you remove, with rm, outside .assay and .claude"
)
BASE64 = "the command decodes base64, which can spell a path or a command the hook cannot see; write it out plainly"
ESCAPES = "the command spells a word in bytes ($'...' quoting, a \\x or an octal escape); spell paths and arguments plainly"
SINGLE = f"the allowed form is a single {LAUNCHER} command, e.g. {LAUNCHER} commit @.assay/model_plan.json"
INSTALLATION = "is part of the harness's installation (the hooks, the launcher, the interpreter, the package); nothing there is yours to write"


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
    CLAUDE_PROJECT_DIR set (or not), PWD the run directory, `ASSAY` not
    exported."""
    environment = dict(os.environ)
    environment.pop("ASSAY", None)
    environment.pop("CLAUDE_PROJECT_DIR", None)
    environment["PWD"] = str(cwd)
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


def _protected() -> list[str]:
    """The installation paths the policy protects, as `hooks install` lists
    them for this interpreter."""
    found: list[Path] = []
    scheme = sysconfig.get_paths()
    for path in (
        HOOK,
        LAUNCHER,
        Path(sys.executable),
        Path(os.path.realpath(sys.executable)),
        REPO / "src",
        *(Path(scheme[key]).resolve() for key in ("stdlib", "platstdlib", "purelib", "platlib") if scheme.get(key)),
    ):
        if path not in found:
            found.append(path)
    return [str(path) for path in found]


# --- the PreToolUse script: writes ---------------------------------------------


def test_a_write_under_the_state_directory_or_the_settings_is_refused_wherever_it_lies(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        _refused(_hook(policy, _write(".assay/events.jsonl", tool), cwd=run, project_dir=run), f".assay/events.jsonl {STATE_TEXT}")
    _refused(
        _hook(policy, _write(str(run / ".assay" / "verifiers" / "v.py")), cwd=run, project_dir=run),
        f"{run / '.assay' / 'verifiers' / 'v.py'} {STATE_TEXT}",
    )
    # The default macOS filesystem folds case: the components are compared lowered.
    _refused(_hook(policy, _write(".ASSAY/events.jsonl"), cwd=run, project_dir=run), f".ASSAY/events.jsonl {STATE_TEXT}")
    _refused(
        _hook(policy, _write(".Claude/settings.json"), cwd=run, project_dir=run),
        f".Claude/settings.json lies under .claude, the operator's settings; nothing there is yours to write, {ELSEWHERE}",
    )
    # A symlink into the state directory resolves to it.
    (run / ".assay").mkdir()
    (run / "link").symlink_to(run / ".assay")
    _refused(_hook(policy, _write("link/events.jsonl"), cwd=run, project_dir=run), f"link/events.jsonl {STATE_TEXT}")
    # The component is refused wherever the path lies: Claude Code reloads a
    # settings edit live, so one write to the user's settings or to
    # .claude.json could disable every non-managed hook of the session.
    home_settings = Path("~/.claude/settings.json").expanduser()
    _refused(
        _hook(policy, _write(str(home_settings)), cwd=run, project_dir=run),
        f"{home_settings} lies under .claude, the operator's settings; nothing there is yours to write, {ELSEWHERE}",
    )
    home_config = Path("~/.claude.json").expanduser()
    _refused(
        _hook(policy, _write(str(home_config)), cwd=run, project_dir=run),
        f"{home_config} is .claude.json, the Claude Code configuration; nothing there is yours to write, {ELSEWHERE}",
    )
    other = tmp_path / "other" / ".assay" / "x"
    _refused(_hook(policy, _write(str(other)), cwd=run, project_dir=run), f"{other} {STATE_TEXT}")
    # An editor call without a path fails closed.
    _refused(
        _hook(policy, {"tool_name": "Edit", "tool_input": {"old_string": "a", "new_string": "b"}, "tool_use_id": "t"}, cwd=run, project_dir=run),
        "the Edit call carries no file path, so the hook cannot read where it writes; name the file",
    )
    _refused(
        _hook(policy, {"tool_name": "MultiEdit", "tool_input": {"files": [{"file_path": ".assay/x"}]}, "tool_use_id": "t"}, cwd=run, project_dir=run),
        "the MultiEdit call carries no file path, so the hook cannot read where it writes; name the file",
    )


def test_a_write_under_the_anchor_directory_the_operators_files_or_the_installation_is_refused(tmp_path):
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
    for target in (HOOK, LAUNCHER, REPO / "src" / "assay" / "cli.py", Path(sys.executable)):
        _refused(_hook(policy, _write(str(target)), cwd=run, project_dir=run), f"{target} {INSTALLATION}, {ELSEWHERE}")


def test_an_edit_of_the_notes_is_allowed_and_paths_resolve_against_the_project_directory(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    # Relative to CLAUDE_PROJECT_DIR, then to the hook's cwd, then realpath;
    # the exception is compared lowered like the rule.
    _allowed(_hook(policy, _write(".assay/NOTES.md", "Edit"), cwd=tmp_path, project_dir=run))
    _allowed(_hook(policy, _write(".assay/NOTES.md", "Edit"), cwd=run, project_dir=None))
    _allowed(_hook(policy, _write(".assay/notes.md", "Edit"), cwd=run, project_dir=run))
    _allowed(_hook(policy, _write(str(run / "sub" / ".." / ".assay" / "NOTES.md"), "Write"), cwd=run, project_dir=run))
    _refused(_hook(policy, _write(".assay/events.jsonl", "Edit"), cwd=run, project_dir=None), f".assay/events.jsonl {STATE_TEXT}")
    # The agent's own files in the run directory.
    _allowed(_hook(policy, _write("checks/first.py"), cwd=run, project_dir=run))
    _allowed(_hook(policy, _write("model.py", "Edit"), cwd=run, project_dir=run))


# --- the PreToolUse script: Bash --------------------------------------------------


def test_rm_rf_and_ln_s_naming_the_state_directory_are_refused(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    for command, first in (
        ("rm -rf .assay", "rm"),
        ("rm -rf .ASSAY", "rm"),
        ("ln -s .assay x", "ln"),
        ("rm -rf ./.assay/", "rm"),
        ('rm -rf "$PWD/.assay"', "rm"),
        ("rm -rf '.assay'", "rm"),
        ("rm -rf .a*", "rm"),
        ("cat .assay/events.jsonl", "cat"),
        ("cp x .claude/settings.json", "cp"),
        ("assay commit @.assay/model_plan.json", "assay"),
        ("bash -c 'rm -rf .assay'", "bash"),
    ):
        what = ".claude (the operator's settings)" if ".claude" in command else ".assay (the run's state directory)"
        _refused(
            _hook(policy, _bash(command), cwd=run, project_dir=run),
            f"the command names {what} and begins with {first!r}, not the launcher; {SINGLE}",
        )
    # A planted symlink resolves into the state directory.
    (run / ".assay").mkdir()
    (run / "link").symlink_to(run / ".assay")
    _refused(
        _hook(policy, _bash("rm -rf link/"), cwd=run, project_dir=run),
        f"the command names .assay (the run's state directory) and begins with 'rm', not the launcher; {SINGLE}",
    )
    # The anchor directory, the policy file and the installation by their
    # paths, relative to the cwd and absolute.
    anchor = _anchor_dir()
    _refused(
        _hook(policy, _bash(f"rm -rf {anchor.name}"), cwd=anchor.parent, project_dir=run),
        f"the command names the anchor directory and begins with 'rm', not the launcher; {SINGLE}",
    )
    _refused(
        _hook(policy, _bash(f"truncate -s 0 {anchor / 'x.jsonl'}"), cwd=run, project_dir=run),
        f"the command names the anchor directory and begins with 'truncate', not the launcher; {SINGLE}",
    )
    _refused(
        _hook(policy, _bash(f"cat {policy}"), cwd=run, project_dir=run),
        f"the command names the hook policy file and begins with 'cat', not the launcher; {SINGLE}",
    )
    for command, first in ((f"cp evil {LAUNCHER}", "cp"), (f"cp evil {HOOK}", "cp"), (f"cp x {sys.executable}", "cp"), (f"cat {HOOK}", "cat")):
        _refused(
            _hook(policy, _bash(command), cwd=run, project_dir=run),
            f"the command names the harness's installation and begins with {first!r}, not the launcher; {SINGLE}",
        )
    # Running the launcher or the interpreter is not naming it.
    _allowed(_hook(policy, _bash(f"{LAUNCHER} status"), cwd=run, project_dir=run))
    _allowed(_hook(policy, _bash(f"{sys.executable} -c 'print(1)'"), cwd=run, project_dir=run))


def test_a_compound_command_naming_the_state_directory_is_refused_whatever_it_begins_with(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    compound = (
        "the command names .assay (the run's state directory) inside a compound command (;, &, &&, "
        f"||, |, a newline, $( or a backtick); {SINGLE}"
    )
    for command in (
        f"{LAUNCHER} status; rm -rf .assay",
        f"{LAUNCHER} status & rm -rf .assay",
        f"{LAUNCHER} status && cat .assay/events.jsonl",
        f"{LAUNCHER} status || rm -rf .assay",
        f"{LAUNCHER} commit @.assay/model_plan.json | tee out.txt",
        f"{LAUNCHER} status\nrm -rf .assay",
        f"{LAUNCHER} view --event $(cat .assay/x)",
        f"{LAUNCHER} view --event `cat .assay/x`",
        f'{LAUNCHER} view --event "$(cat .assay/x)"',
        "cd .assay; ls",
    ):
        _refused(_hook(policy, _bash(command), cwd=run, project_dir=run), compound)
    _refused(
        _hook(policy, _bash(f"{LAUNCHER} status > .assay/events.jsonl"), cwd=run, project_dir=run),
        f"the command names .assay (the run's state directory) beside a redirection; {SINGLE}, with no redirection",
    )
    _refused(
        _hook(policy, _bash(f"{LAUNCHER} commit @.assay/model_plan.json 'unterminated"), cwd=run, project_dir=run),
        "the command names .assay (the run's state directory) and does not parse as one shell "
        f"command (No closing quotation); {SINGLE}",
    )


def test_a_single_launcher_command_naming_the_state_directory_is_allowed(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    _allowed(_hook(policy, _bash(f"{LAUNCHER} commit @.assay/model_plan.json"), cwd=run, project_dir=run))
    _allowed(_hook(policy, _bash(f'{LAUNCHER} act RUN --params @.assay/params.json --predict "change"'), cwd=run, project_dir=run))
    # The operators are tested outside quotes: the documented forms with a
    # two-claim prediction or a reason holding a pipe are single commands.
    _allowed(_hook(policy, _bash(f'{LAUNCHER} act RUN --params @.assay/params.json --predict "change; ch counter delta = 1"'), cwd=run, project_dir=run))
    _allowed(_hook(policy, _bash(f'{LAUNCHER} act RUN --params @.assay/params.json --predict "change" --because "a | b"'), cwd=run, project_dir=run))
    # The launcher's python and export sub-commands run code or write a
    # file where the agent says: never exempt.
    for subcommand, command in (
        ("python", f"{LAUNCHER} python \"__import__('shutil').rmtree('.assay')\""),
        ("export", f"{LAUNCHER} export --out .assay/events.jsonl"),
        ("export", f"{LAUNCHER} --run-dir . --json export --out=.assay/events.jsonl"),
    ):
        _refused(
            _hook(policy, _bash(command), cwd=run, project_dir=run),
            f"the command names .assay (the run's state directory) through the launcher's {subcommand} "
            "sub-command, which runs code or writes a file where the agent says; the allowed form is a "
            f"single launcher command of another kind, e.g. {LAUNCHER} commit @.assay/model_plan.json",
        )
    # The variable is not trusted on its own: the policy must pin the value
    # the operator exported before the session.
    _refused(
        _hook(policy, _bash('"$ASSAY" commit @.assay/model_plan.json'), cwd=run, project_dir=run),
        f"the command names .assay (the run's state directory) and begins with '$ASSAY', not the launcher; {SINGLE}",
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
    for command in (
        "export ASSAY=/tmp/x",
        "ASSAY=/tmp/x",
        "PATH=/tmp:$PATH",
        "export PATH",
        "unset ASSAY",
        "alias assay=/tmp/x",
        "assay() { /tmp/x \"$@\"; }",
        "function assay { :; }",
        f"function {LAUNCHER} {{ :; }}",
        f"{LAUNCHER}() {{ :; }}",
        "function /opt/bin/assay { :; }",
        "declare -x ASSAY=/tmp/x",
        "env ASSAY=/tmp/x /tmp/y status",
        "echo ${ASSAY:=/tmp/x}",
        "read -r ASSAY",
        "printf -v ASSAY /tmp/x",
        "mapfile -t ASSAY < x",
        "export ASSAY_PYTHON=/tmp/x",
        "export PYTHONPATH=/tmp/x",
        "PYTHONPATH=src python3 -c 'print(1)'",
        "PROMPT_COMMAND='rm -rf .assay'",
        "BASH_ENV=/tmp/x bash",
        "LD_PRELOAD=/tmp/x.so ls",
        "DYLD_INSERT_LIBRARIES=/tmp/x.dylib ls",
    ):
        _refused(_hook(policy, _bash(command), cwd=run, project_dir=run), SHELL_STATE)
    _allowed(_hook(policy, _bash('echo "$ASSAY" "$PATH"'), cwd=run, project_dir=run))
    _allowed(_hook(policy, _bash("grep -r 'PATH=' file"), cwd=run, project_dir=run))
    _allowed(_hook(policy, _bash(f'{LAUNCHER} act RUN --predict "change" --because "export PATH first"'), cwd=run, project_dir=run))


def test_the_verbs_that_run_unseen_text_or_remove_by_rule_are_refused(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    for command in (
        'eval "$(echo x)"',
        "source ./env.sh",
        ". ./env.sh",
        "exec /bin/sh",
        "ls | xargs rm",
        "trap 'rm -rf .assay' DEBUG",
        "sudo -u x eval ls",
    ):
        _refused(_hook(policy, _bash(command), cwd=run, project_dir=run), VERBS)
    for command in ("find . -name '*.pyc' -delete", "find . -exec rm {} +", "git clean -fdx", "git -C . clean -f", "rsync -a --delete a b"):
        _refused(_hook(policy, _bash(command), cwd=run, project_dir=run), BY_RULE)
    for command in ("echo eC | base64 -d", "base64 --decode x.b64"):
        _refused(_hook(policy, _bash(command), cwd=run, project_dir=run), BASE64)
    for command in ("echo $'\\x2eassay'", "printf '\\056assay'", 'rm -rf "\\x2eassay"', "sed -E 's/(x)/\\1/' f"):
        _refused(_hook(policy, _bash(command), cwd=run, project_dir=run), ESCAPES)
    _allowed(_hook(policy, _bash("./run.sh"), cwd=run, project_dir=run))
    _allowed(_hook(policy, _bash("find . -name '*.py'"), cwd=run, project_dir=run))
    _allowed(_hook(policy, _bash("base64 x.bin"), cwd=run, project_dir=run))


def test_the_run_directory_and_its_ancestors_are_refused_to_the_destructive_verbs(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)

    def line(verb: str, word: str) -> str:
        return (
            "the command removes, moves or re-permissions the run directory or a directory above it "
            f"({verb} {word}); the run directory is the operator's, work inside it"
        )

    _refused(_hook(policy, _bash(f"rm -rf {run}"), cwd=run, project_dir=run), line("rm", str(run)))
    _refused(_hook(policy, _bash('rm -rf "$PWD"'), cwd=run, project_dir=run), line("rm", "$PWD"))
    _refused(_hook(policy, _bash("rm -rf ."), cwd=run, project_dir=run), line("rm", "."))
    _refused(_hook(policy, _bash("cd .. && rm -rf run"), cwd=run, project_dir=run), line("rm", "run"))
    _refused(_hook(policy, _bash("cd ..\nrm -rf run"), cwd=run, project_dir=run), line("rm", "run"))
    _refused(_hook(policy, _bash("chmod -R 000 ."), cwd=run, project_dir=run), line("chmod", "."))
    _refused(_hook(policy, _bash(f"mv {run} /tmp/elsewhere"), cwd=run, project_dir=run), line("mv", str(run)))
    _refused(_hook(policy, _bash(f"rm -rf {tmp_path}"), cwd=run, project_dir=run), line("rm", str(tmp_path)))
    _refused(_hook(policy, _bash("sudo rm -rf ../run"), cwd=run, project_dir=run), line("rm", "../run"))
    # The agent's own files and directories inside the run are its to remove.
    _allowed(_hook(policy, _bash("rm -rf scratch"), cwd=run, project_dir=run))
    _allowed(_hook(policy, _bash("mv checks/a.py checks/b.py"), cwd=run, project_dir=run))


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
        "python3 checks/first.py && echo ok",
        "echo 'a; b | c' > notes.txt",
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
    (tmp_path / "shape.json").write_text(json.dumps({"version": 1}))
    result = _hook(tmp_path / "shape.json", _bash("ls"), cwd=run, project_dir=run)
    assert result.returncode == 2
    assert result.stderr == (
        f"HOOK | REFUSED | the hook policy file {tmp_path / 'shape.json'} is not a policy file "
        "(version 1 is not 2); the operator reinstalls the hooks with `assay hooks install "
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
    assert json.loads(policy.read_text()) == {
        "version": 2,
        "run_dir": str(run.resolve()),
        "anchor_dir": str(_anchor_dir()),
        "token_file": str(token.resolve()),
        "policy_file": str(policy.resolve()),
        "launcher": str(LAUNCHER),
        "assay_value": None,
        "deny": ["world_client"],
        "protected": _protected(),
        "python": sys.executable,
    }
    pre = f"{sys.executable} {HOOK} --policy {policy}"
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
    bare = _cli(run, "hooks", "install")
    assert bare.returncode == 2
    assert bare.stderr.startswith("ERROR | COMMAND_ARGS | hooks install needs --policy FILE, the policy's place outside the run directory\n")


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
    assert commands == [f"{sys.executable} {HOOK} --policy {moved}"] * 5


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


def test_hooks_install_writes_through_no_symlink(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    elsewhere = tmp_path / "agent-dir"
    elsewhere.mkdir()
    (run / ".claude").symlink_to(elsewhere)
    policy = tmp_path / "operator" / "policy.json"
    refused = _cli(run, "hooks", "install", "--policy", str(policy))
    assert refused.returncode == 2
    assert refused.stderr == (
        f"ERROR | PATH_INVALID | {run / '.claude'} is a symlink to {elsewhere}, and the install "
        "writes nothing through one\nNEXT | replace it with a real directory or file, then install again\n"
    )
    assert list(elsewhere.iterdir()) == [] and not policy.exists()
    (run / ".claude").unlink()
    (run / ".claude").mkdir()
    (run / ".claude" / "settings.json").symlink_to(elsewhere / "settings.json")
    refused = _cli(run, "hooks", "install", "--policy", str(policy))
    assert refused.returncode == 2
    assert refused.stderr.startswith(f"ERROR | PATH_INVALID | {run / '.claude' / 'settings.json'} is a symlink to ")
    assert not (elsewhere / "settings.json").exists() and not policy.exists()


def test_the_check_and_doctor_run_the_pinned_interpreter_on_the_script(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    ok_line = f"policy {policy}; interpreter {sys.executable} runs {HOOK}; launcher {LAUNCHER}"
    checked = _cli(run, "hooks", "install", "--check")
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout == f"HOOKS | ok | {ok_line}\n"
    doctor = _cli(run, "doctor")
    assert doctor.returncode == 0, doctor.stderr
    assert f"DOCTOR | ok | hooks | {ok_line}\n" in doctor.stdout
    refused = _cli(run, "hooks", "install", "--check", "--policy", str(policy))
    assert refused.returncode == 2
    assert refused.stderr == "ERROR | COMMAND_ARGS | --check takes no other flag: it verifies the hooks installed in this run directory\n"
    # A hook that cannot start is a non-blocking error to Claude Code: the
    # check says so instead of the mechanism failing open without a word.
    document = json.loads(policy.read_text())
    document["python"] = str(tmp_path / "gone" / "python3")
    policy.write_text(json.dumps(document))
    broken = _cli(run, "hooks", "install", "--check")
    assert broken.returncode == 2
    assert broken.stderr == (
        f"ERROR | HOOK_CHECK_FAILED | the pinned interpreter {tmp_path / 'gone' / 'python3'} is not an "
        "executable file\nNEXT | reinstall the hooks with `assay hooks install --policy FILE ...` "
        "from the operator's shell\n"
    )
    doctor = _cli(run, "doctor")
    assert doctor.returncode == 2
    assert f"DOCTOR | FAIL | hooks | the pinned interpreter {tmp_path / 'gone' / 'python3'} is not an executable file\n" in doctor.stdout
    document["python"] = sys.executable
    document["version"] = 1
    policy.write_text(json.dumps(document))
    broken = _cli(run, "hooks", "install", "--check")
    assert broken.returncode == 2
    assert broken.stderr.startswith(f"ERROR | HOOK_POLICY_INVALID | the hook policy file {policy} is not a policy file: version 1 is not 2\n")
    # Without hooks there is nothing to check, and doctor says nothing about them.
    bare = tmp_path / "bare"
    bare.mkdir()
    nothing = _cli(bare, "hooks", "install", "--check")
    assert nothing.returncode == 2
    assert nothing.stderr.startswith(f"ERROR | HOOK_CHECK_FAILED | no hooks are installed in {bare.resolve()} (no harness entry in .claude/settings.json)\n")
    assert "hooks |" not in _cli(bare, "doctor").stdout


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


def _bash_response(stdout: str) -> dict:
    return {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False}


def test_the_post_tool_use_record_joins_by_end_event(tmp_path):
    run = tmp_path / "run"
    _, policy = _install(tmp_path, run)
    _run_state(run)
    json_receipt = json.dumps(
        {"kind": "act", "outcome": "PREDICTED", "detail": "all 1 claims held", "start_event": 4, "end_event": 5, "estimated_tokens": 40}
    )
    mcp_record = json.dumps({"receipt": {"kind": "act", "outcome": "SURPRISE", "start_event": 6, "end_event": 7}, "text": PROSE_RECEIPT})
    long_command = "echo " + "x" * 100
    for payload in (
        {"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": f"{LAUNCHER} act INC amount=1 --predict change"},
         "tool_response": _bash_response(PROSE_RECEIPT)},
        {"tool_name": "Bash", "tool_use_id": "t2", "tool_input": {"command": f"{LAUNCHER} act INC amount=1 --predict change --json"},
         "tool_response": _bash_response(json_receipt + "\n")},
        {"tool_name": "Bash", "tool_use_id": "t3", "tool_input": {"command": "ls -la"},
         "tool_response": _bash_response("total 0\n")},
        {"tool_name": "mcp__assay__act", "tool_use_id": "t4", "tool_input": {"action": "INC", "params": {"amount": 1}, "predict": "change"},
         "tool_response": [{"type": "text", "text": mcp_record}]},
        {"tool_name": "Edit", "tool_use_id": "t5", "tool_input": {"file_path": ".assay/NOTES.md", "old_string": "a", "new_string": "b"},
         "tool_response": {"filePath": ".assay/NOTES.md", "type": "update"}},
        {"tool_name": "Bash", "tool_use_id": "t6", "tool_input": {"command": long_command},
         "tool_response": _bash_response("")},
        # Only the top level of an act, commit or reset document counts: a
        # field nested elsewhere, or on a document of another kind, is not
        # a receipt's.
        {"tool_name": "Bash", "tool_use_id": "t7", "tool_input": {"command": f"{LAUNCHER} status --json"},
         "tool_response": _bash_response(json.dumps({"kind": "status", "recent": [{"end_event": 9}], "end_event": 8}) + "\n")},
        {"tool_name": "mcp__assay__status", "tool_use_id": "t8", "tool_input": {},
         "tool_response": [{"type": "text", "text": json.dumps({"history": {"receipt": {"kind": "act", "end_event": 9}}})}]},
    ):
        result = _post(policy, payload, run)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "" and result.stderr == ""
    records = _records(run)
    assert [sorted(record) for record in records] == [
        ["command_prefix", "end_event", "kind", "session_id", "timestamp", "tool", "tool_use_id"]
    ] * 8
    assert [(r["kind"], r["session_id"], r["tool_use_id"], r["tool"], r["end_event"]) for r in records] == [
        ("tool_use", "s1", "t1", "Bash", 3),
        ("tool_use", "s1", "t2", "Bash", 5),
        ("tool_use", "s1", "t3", "Bash", None),
        ("tool_use", "s1", "t4", "mcp__assay__act", 7),
        ("tool_use", "s1", "t5", "Edit", None),
        ("tool_use", "s1", "t6", "Bash", None),
        ("tool_use", "s1", "t7", "Bash", None),
        ("tool_use", "s1", "t8", "mcp__assay__status", None),
    ]
    assert records[0]["command_prefix"] == f"{LAUNCHER} act INC amount=1 --predict change"[:80]
    assert records[2]["command_prefix"] == "ls -la"
    assert records[3]["command_prefix"] == "mcp__assay__act"
    assert records[4]["command_prefix"] == ".assay/NOTES.md"
    assert records[5]["command_prefix"] == long_command[:80] and len(records[5]["command_prefix"]) == 80
    quiet = _cli(run, "hooks", "post-tool-use", "--policy", str(policy), "--json",
                 stdin=json.dumps({"session_id": "s1", "tool_name": "Bash", "tool_use_id": "t9", "tool_input": {"command": "ls"}}))
    assert quiet.returncode == 0, quiet.stderr
    assert quiet.stdout == '{"lines":[]}\n'
    assert len(_records(run)) == 9


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


# --- the kernel's side ------------------------------------------------------------------


def test_the_export_refuses_a_target_under_the_state_or_the_settings_whatever_the_hooks(paths):
    from assay.carryover import export_knowledge
    from assay.core import AssayError

    run = run_of(paths)
    for target in (paths.state / "events.jsonl", paths.root / ".claude" / "settings.json", paths.state / "sub" / "k.json"):
        with pytest.raises(AssayError) as refused:
            export_knowledge(run, target)
        assert refused.value.code == "PATH_INVALID"
        assert str(refused.value) == f"--out must not point under {target.parent.name if target.parent.name.startswith('.') else '.assay'}, got {target}"
    assert not (paths.root / ".claude").exists()
    assert not paths.activity.exists()
    exported = export_knowledge(run, paths.root / "knowledge.json")
    assert exported == paths.root / "knowledge.json" and exported.exists()


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


@pytest.mark.parametrize(
    ("command", "usage"),
    [("hooks install", "usage: assay hooks install [-h] [--policy FILE]"), ("hooks post-tool-use", "usage: assay hooks post-tool-use [-h] --policy FILE")],
)
def test_the_hook_commands_render_their_help(command, usage, tmp_path):
    result = _cli(tmp_path, *command.split(), "--help")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(usage)

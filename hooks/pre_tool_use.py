#!/usr/bin/env python3
"""The PreToolUse hook of a Claude Code session on an ASSAY run directory
(docs/ARCHITECTURE.md section 8.4): the operator's tool policy, refused
before the tool runs.

`assay hooks install --policy FILE` writes the policy into FILE, outside the
run directory (the run directory, the anchor directory, the owner token file,
FILE's own path, the pinned launcher, the deny patterns, the protected
installation paths, the interpreter), and the hook entries into
`<run dir>/.claude/settings.json`, each carrying FILE's path. This script
reads the hook's JSON on stdin and the policy from FILE. It runs under the
interpreter pinned at install, in Claude Code's own process environment, with
the standard library alone: it imports nothing from `assay`, so it works
whatever the agent did to its shell or to the packages on its path.

The rules, in the order they are applied:

- `Write`, `Edit`, `MultiEdit`, `NotebookEdit`: the file path (`file_path`,
  or `notebook_path`), resolved against `CLAUDE_PROJECT_DIR` (then the hook's
  `cwd`) and through realpath, is refused when any of its components, compared
  lowered since the default macOS filesystem folds case, is `.assay`,
  `.claude` or `.claude.json` (except the run's own `.assay/NOTES.md`, the
  agent's file), when it lies under the anchor directory, when it is the
  owner token file or the policy file, or when it is one of the protected
  installation paths (the hook script, the launcher, the interpreter, the
  harness's package directory, the interpreter's library directories). A
  call without a path is refused.
- `Bash`: refused outright are `$'...'` quoting and a `\\x` or octal escape
  in a word (a path spelled in bytes); a change to the shell state the trust
  in the launcher rests on (an assignment, export, unset or read of
  `ASSAY*`, `PYTHON*`, `PATH`, `BASH_ENV`, `ENV`, `PROMPT_COMMAND`,
  `LD_PRELOAD` or `DYLD_*`, an alias of `assay`, a function named `assay`,
  ending in `/assay` or named as the launcher); `eval`, `source`, `. FILE`,
  `exec`, `xargs` and `trap` as a command, `find` with `-delete` or `-exec`,
  `git clean`, `rsync --delete` and `base64` decoding; a command matching a
  deny pattern (the operator's regular expressions: the world's client
  libraries, hosts and commands); and `rm`, `rmdir`, `mv`, `chmod`, `chown`
  or `chgrp` of the run directory or a directory above it, with `cd`
  followed through a compound command. A command that names a refused path
  (a word with a refused component, a word that resolves under the run's
  `.assay` or `.claude`, under the anchor directory, or to the token file,
  the policy file or a protected installation path, relative names against
  the cwd) is refused unless it is a single simple command whose first word
  is the pinned launcher, or `"$ASSAY"` when the policy pins the value the
  operator exported, and whose sub-command is not `python` or `export`: a
  compound command (`;`, `&`, `&&`, `|`, `||`, a newline, `$(` or a
  backtick, tested outside quotes), a redirection, or a command shlex cannot
  parse is refused whatever it begins with. Running the launcher or the
  interpreter is not naming it.

A refusal is one line on stderr, `HOOK | REFUSED | <rule>; <allowed form>`,
with exit status 2, which Claude Code shows to the agent as the reason the
call was denied; everything else is exit status 0 with no output. The policy
is a text match over a command the agent's own shell will run: the
operator's tool policy made explicit, not a sandbox.
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import json
import os
import re
import shlex
import sys
from typing import Any

POLICY_VERSION = 2
REFUSED = "HOOK | REFUSED | "
EDITORS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
STATE = ".assay"
SETTINGS = ".claude"
USER_SETTINGS = ".claude.json"
COMPONENTS = {
    STATE: ".assay (the run's state directory)",
    SETTINGS: ".claude (the operator's settings)",
    USER_SETTINGS: ".claude.json (the Claude Code user configuration)",
}
NOTES = "NOTES.md"
# The characters that make a command compound (section 8.4), tested outside
# quotes: `;`, `&`, `&&`, `|`, `||`, a newline, `$(` and a backtick.
COMPOUND = re.compile(r"[;|&\n`]|\$\(")
# A word that is a redirection, after shlex: `>`, `>>`, `2>`, `<`, `&>`,
# `>file`, `2>&1`.
REDIRECTION = re.compile(r"^(\d*[<>]|&>)")
# A path spelled in bytes: `$'...'` quoting, a `\x` or an octal escape.
ESCAPES = re.compile(r"\$'|\\x[0-9A-Fa-f]|\\[0-7]{1,3}")
# The variables the trust in the launcher rests on: the launcher's own, the
# interpreter's, the lookup path, and what a shell or the loader reads before
# any command runs.
NAMES = r"(?:ASSAY\w*|PYTHON\w*|PATH|BASH_ENV|ENV|PROMPT_COMMAND|LD_PRELOAD|DYLD_\w+)"
# A change to the shell state: an assignment (bare, exported, `env`,
# `declare -x`, `+=`, `${NAME:=...}`), an export, unset, declare, typeset,
# readonly, local, read, mapfile or readarray naming one, `printf -v` into
# one, and an alias of assay.
SHELL_STATE = re.compile(
    rf"(?<![\w$]){NAMES}(?:\[[^\]]*\])?[+:]?="
    rf"|\$\{{{NAMES}:?="
    rf"|\b(?:export|unset|declare|typeset|readonly|local|read|mapfile|readarray)\b[^;&|\n]*?(?<![\w$]){NAMES}\b"
    rf"|\bprintf\s+-v\s+{NAMES}\b"
    r"|\balias\s+assay\b"
)
# The words that may stand before the command word of a simple command.
PREFIXES = frozenset({"sudo", "doas", "env", "nohup", "time", "command", "builtin", "nice", "stdbuf", "caffeinate"})
ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
# A command position in the text with its quotes blanked: the start, or after
# an operator, then any prefixes with their flags and assignments.
COMMAND_POSITION = (
    r"(?:^|[;&|(`\n])\s*(?:\w+=\S*\s+)*"
    r"(?:(?:sudo|doas|env|nohup|time|command|builtin|nice|stdbuf|caffeinate)\s+(?:-\S+(?:\s+[^\s-]\S*)?\s+)*(?:\w+=\S*\s+)*)*"
)
# The verbs refused as a command, whatever follows: they run text the hook
# cannot see, or remove by rule.
VERBS = re.compile(COMMAND_POSITION + r"(?:eval|source|\.|exec|xargs|trap)(?=\s|$)")
FIND = re.compile(r"\bfind\b[^;&|\n]*\s-(?:delete|exec|execdir|ok|okdir)\b")
GIT_CLEAN = re.compile(r"\bgit\b(?:\s+-\S+(?:\s+[^\s-]\S*)?)*\s+clean\b")
RSYNC = re.compile(r"\brsync\b[^;&|\n]*\s--delete")
BASE64 = re.compile(r"\bbase64\b[^;&|\n]*\s(?:-d|-D|--decode)\b")
# The verbs that remove, move or re-permission what they name.
DESTRUCTIVE = frozenset({"rm", "rmdir", "mv", "chmod", "chown", "chgrp", "chflags", "chattr"})
# The tokens shlex's punctuation mode emits between simple commands.
SEPARATORS = frozenset({";", "&", "&&", "|", "||", "\n", "(", ")"})
GLOB = frozenset("*?[")
INSTALLATION = "the harness's installation"


@dataclasses.dataclass(frozen=True)
class Policy:
    """The policy file as `assay hooks install` wrote it, paths resolved."""

    run_dir: str
    anchor_dir: str
    token_file: str | None
    policy_file: str
    launcher: str
    assay_value: str | None
    deny: tuple[str, ...]
    protected: tuple[str, ...]
    python: str


def _string(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(obj: dict[str, Any], key: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string or null")
    return value


def _strings(obj: dict[str, Any], key: str) -> tuple[str, ...]:
    value = obj.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return tuple(value)


def load_policy(path: str) -> tuple[Policy | None, str]:
    """The policy, or None and the problem: a file that cannot be read or
    does not hold the shape `hooks install` writes fails closed."""
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError) as error:
        return None, f"cannot be read ({type(error).__name__}: {error})"
    try:
        if not isinstance(value, dict):
            raise ValueError("the policy is not a JSON object")
        if value.get("version") != POLICY_VERSION:
            raise ValueError(f"version {value.get('version')!r} is not {POLICY_VERSION}")
        policy = Policy(
            run_dir=_string(value, "run_dir"),
            anchor_dir=_string(value, "anchor_dir"),
            token_file=_optional_string(value, "token_file"),
            policy_file=_string(value, "policy_file"),
            launcher=_string(value, "launcher"),
            assay_value=_optional_string(value, "assay_value"),
            deny=_strings(value, "deny"),
            protected=_strings(value, "protected"),
            python=_string(value, "python"),
        )
    except ValueError as error:
        return None, f"is not a policy file ({error})"
    return policy, ""


# --- paths --------------------------------------------------------------------


def _one_line(text: str) -> str:
    return text.replace("\r", "\\r").replace("\n", "\\n")


def _under(path: str, directory: str) -> bool:
    """Whether `path` is `directory` or lies under it, case folded (both
    resolved)."""
    low, root = path.lower(), directory.lower().rstrip(os.sep)
    return low == root or low.startswith(root + os.sep)


def _resolve(raw: str, base: str) -> str:
    expanded = os.path.expandvars(os.path.expanduser(raw))
    return os.path.realpath(os.path.join(base, expanded))


def _component_refusal(parts: list[str]) -> str | None:
    """What a path with these components names, when a component is one of
    the refused ones (case folded), or one that a glob would expand to."""
    for component in parts:
        low = component.lower()
        what = COMPONENTS.get(low)
        if what is not None:
            return what
        if low.startswith(".") and GLOB & set(low):
            for name, named in COMPONENTS.items():
                if fnmatch.fnmatchcase(name, low):
                    return named
    return None


def _protected_refusal(policy: Policy, resolved: str) -> str | None:
    """What a resolved path is, when it is one of the operator's: under the
    run's own `.assay` or `.claude` (a symlink resolves there), under the
    anchor directory, the owner token file, the policy file, or one of the
    installation paths; else None."""
    if _under(resolved, os.path.join(policy.run_dir, STATE)):
        return COMPONENTS[STATE]
    if _under(resolved, os.path.join(policy.run_dir, SETTINGS)):
        return COMPONENTS[SETTINGS]
    if _under(resolved, policy.anchor_dir):
        return "the anchor directory"
    if policy.token_file is not None and resolved.lower() == policy.token_file.lower():
        return "the owner token file"
    if resolved.lower() == policy.policy_file.lower():
        return "the hook policy file"
    for path in policy.protected:
        if _under(resolved, path):
            return INSTALLATION
    return None


def _is_notes(policy: Policy, resolved: str) -> bool:
    return resolved.lower() == os.path.join(policy.run_dir, STATE, NOTES).lower()


ELSEWHERE = "your files go in the run directory outside .assay and .claude"


def editor_refusal(policy: Policy, tool: str, raw: str | None, base: str) -> str | None:
    """The refusal of a Write, Edit, MultiEdit or NotebookEdit, or None."""
    if not raw:
        return f"the {tool} call carries no file path, so the hook cannot read where it writes; name the file"
    resolved = _resolve(raw, base)
    shown = _one_line(raw)
    if _is_notes(policy, resolved):
        return None
    what = _component_refusal([part for part in resolved.split(os.sep) if part])
    if what is None:
        what = _protected_refusal(policy, resolved)
    if what is None:
        return None
    if what == COMPONENTS[STATE]:
        return (
            f"{shown} lies under .assay, which the daemon writes; .assay/NOTES.md is the "
            "one file there that is yours, the rest is reached through assay"
        )
    if what == COMPONENTS[SETTINGS]:
        return f"{shown} lies under .claude, the operator's settings; nothing there is yours to write, {ELSEWHERE}"
    if what == COMPONENTS[USER_SETTINGS]:
        return f"{shown} is .claude.json, the Claude Code configuration; nothing there is yours to write, {ELSEWHERE}"
    if what == "the anchor directory":
        return f"{shown} lies under the anchor directory, the operator's record of the chain heads; nothing there is yours to write, {ELSEWHERE}"
    if what == INSTALLATION:
        return f"{shown} is part of the harness's installation (the hooks, the launcher, the interpreter, the package); nothing there is yours to write, {ELSEWHERE}"
    return f"{shown} is {what}, which the operator holds; nothing there is yours to write, {ELSEWHERE}"


# --- the Bash command -------------------------------------------------------------


def _blank_quotes(command: str) -> str:
    """The command with the inside of its quoted strings replaced by spaces,
    so the operators and the words are tested outside quotes; `$`, `(` and
    the backtick stay visible inside double quotes, where they still
    substitute a command. An escaped character is blanked too."""
    out: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote is None:
            if char == "\\" and index + 1 < len(command):
                out.append("  ")
                index += 2
                continue
            if char in "'\"":
                quote = char
            out.append(char)
        elif quote == "'":
            if char == "'":
                quote = None
                out.append(char)
            else:
                out.append(" ")
        else:
            if char == "\\" and index + 1 < len(command):
                out.append("  ")
                index += 2
                continue
            if char == '"':
                quote = None
                out.append(char)
            elif char in "$(`":
                out.append(char)
            else:
                out.append(" ")
        index += 1
    return "".join(out)


def _function_rule(policy: Policy) -> re.Pattern[str]:
    """A function named `assay`, ending in `/assay`, or named as the
    launcher: the trusted first word redirected."""
    names = r"(?:\S*/)?assay|" + re.escape(policy.launcher)
    return re.compile(rf"\bfunction\s+(?:{names})\b|(?<![\w.-])(?:{names})\s*\(\s*\)")


def _tokens(command: str) -> list[str] | None:
    """The shell's reading with the operators as their own tokens (shlex's
    punctuation mode, a newline among them), or None when it cannot parse."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None


def _segments(tokens: list[str]) -> list[list[str]]:
    """The simple commands of a token list, split at the separators."""
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in SEPARATORS:
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(token)
    return [segment for segment in segments if segment]


def _command_index(segment: list[str]) -> int | None:
    """The index of the command word: after the leading assignments and the
    prefixes (`sudo`, `env` and the rest) with their flags and assignments."""
    index = 0
    while index < len(segment):
        word = segment[index]
        if ASSIGNMENT.match(word):
            index += 1
            continue
        if word in PREFIXES:
            index += 1
            while index < len(segment) and (segment[index].startswith("-") or ASSIGNMENT.match(segment[index])):
                index += 1
            continue
        return index
    return None


def _path_of_word(word: str) -> str:
    """The path a word may name: the value of a `KEY=VALUE` word, without
    the `@` of the command line's `@FILE` form."""
    key, separator, value = word.partition("=")
    if separator and "/" not in key:
        word = value
    return word[1:] if word.startswith("@") else word


def _word_refusal(policy: Policy, word: str, base: str, *, command_position: bool) -> str | None:
    """What refused path a word names, or None: a refused component, or a
    path that resolves to one of the operator's. Running the launcher or
    the interpreter, as the command word, is not naming it."""
    candidate = _path_of_word(word)
    if not candidate:
        return None
    what = _component_refusal(candidate.split("/"))
    if what is not None:
        return what
    resolved = _resolve(candidate, base)
    what = _protected_refusal(policy, resolved)
    if what == INSTALLATION and command_position:
        low = resolved.lower()
        if low in (policy.launcher.lower(), policy.python.lower(), os.path.realpath(policy.python).lower()):
            return None
    return what


def _ancestor_or_run(policy: Policy, resolved: str) -> bool:
    """Whether a resolved path is the run directory or a directory above it."""
    return _under(policy.run_dir, resolved)


def _walk(policy: Policy, tokens: list[str], cwd: str) -> tuple[str | None, str | None]:
    """The refusals found over the simple commands of a token list, `cd`
    followed from one to the next: (what refused path a word names, the
    destructive refusal)."""
    named: str | None = None
    current = cwd
    for segment in _segments(tokens):
        index = _command_index(segment)
        if index is None:
            continue
        verb = segment[index]
        arguments = [word for word in segment[index + 1 :] if not word.startswith("-")]
        if verb in ("cd", "pushd"):
            target = arguments[0] if arguments else "~"
            if target != "-":
                current = _resolve(target, current)
            continue
        if verb in DESTRUCTIVE:
            for word in arguments:
                if _ancestor_or_run(policy, _resolve(word, current)):
                    return named, (
                        f"the command removes, moves or re-permissions the run directory or a "
                        f"directory above it ({verb} {_one_line(word)}); the run directory is the "
                        "operator's, work inside it"
                    )
        if named is None:
            for position, word in enumerate(segment):
                named = _word_refusal(policy, word, current, command_position=position == index)
                if named is not None:
                    break
    return named, None


def _raw_named(policy: Policy, command: str, cwd: str) -> str | None:
    """The same question over a second reading of the words: the raw text
    split on whitespace, the shell's operators and quotes (so a word inside
    a quoted string, `bash -c 'rm -rf .assay'`, is still seen), and shlex's
    plain reading when it parses (so `".ass""ay"` is `.assay`)."""
    raw = [token for token in re.split(r"[\s;&|()<>'\"`]+", command) if token]
    readings = [raw]
    try:
        readings.append(shlex.split(command))
    except ValueError:
        pass
    for words in readings:
        for position, word in enumerate(words):
            what = _word_refusal(policy, word, cwd, command_position=position == 0)
            if what is not None:
                return what
    return None


def _subcommand(words: list[str]) -> str | None:
    """The launcher's sub-command on a simple command: the first word after
    the launcher that is not a flag or `--run-dir`'s value."""
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--run-dir":
            index += 2
            continue
        if word.startswith("-"):
            index += 1
            continue
        return word
    return None


def _launcher_forms(policy: Policy) -> str:
    if policy.assay_value is not None:
        return f'{policy.launcher} (or "$ASSAY", the launcher the operator exported)'
    return policy.launcher


def bash_refusal(policy: Policy, command: str, cwd: str) -> str | None:
    """The refusal of a Bash command, or None."""
    blanked = _blank_quotes(command)
    if ESCAPES.search(command):
        return (
            "the command spells a word in bytes ($'...' quoting, a \\x or an octal escape); "
            "spell paths and arguments plainly"
        )
    if SHELL_STATE.search(blanked) or _function_rule(policy).search(blanked):
        return (
            "the command changes the shell state the launcher's trust rests on (an assignment, "
            "export, unset or read of ASSAY*, PYTHON*, PATH, BASH_ENV, ENV, PROMPT_COMMAND, "
            "LD_PRELOAD or DYLD_*, an alias of assay, or a function named assay or as the "
            f"launcher); the launcher is {_launcher_forms(policy)}, run it by that path and "
            "leave the shell as it is"
        )
    if VERBS.search(blanked):
        return (
            "the command runs eval, source (or . FILE), exec, xargs or trap, which run text the "
            "hook cannot see; write the command out plainly, one simple command at a time"
        )
    if FIND.search(blanked) or GIT_CLEAN.search(blanked) or RSYNC.search(blanked):
        return (
            "the command removes by rule (find -delete or -exec, git clean, rsync --delete), "
            "which the hook cannot follow; name what you remove, with rm, outside .assay and .claude"
        )
    if BASE64.search(blanked):
        return "the command decodes base64, which can spell a path or a command the hook cannot see; write it out plainly"
    for pattern in policy.deny:
        try:
            hit = re.search(pattern, command)
        except re.error as error:
            return (
                f"the deny pattern {pattern!r} does not compile ({error}); the operator "
                "reinstalls the hooks with a valid pattern"
            )
        if hit:
            return (
                f"the command matches the operator's deny pattern {pattern!r}: the world is "
                f"reached only through the harness; the allowed form is {policy.launcher} act "
                'NAME pname=value --predict "..."'
            )
    tokens = _tokens(command)
    named: str | None = None
    if tokens is not None:
        named, destructive = _walk(policy, tokens, cwd)
        if destructive is not None:
            return destructive
    if named is None:
        named = _raw_named(policy, command, cwd)
    if named is None:
        return None
    single = (
        f"the allowed form is a single {_launcher_forms(policy)} command, e.g. "
        f"{policy.launcher} commit @.assay/model_plan.json"
    )
    if COMPOUND.search(blanked):
        return (
            f"the command names {named} inside a compound command (;, &, &&, ||, |, a newline, "
            f"$( or a backtick); {single}"
        )
    try:
        words = shlex.split(command)
    except ValueError as error:
        return f"the command names {named} and does not parse as one shell command ({error}); {single}"
    if not words:
        return None
    if any(REDIRECTION.match(word) for word in words[1:]):
        return f"the command names {named} beside a redirection; {single}, with no redirection"
    first = words[0]
    trusted = first == policy.launcher or (policy.assay_value is not None and first in ("$ASSAY", "${ASSAY}"))
    if not trusted:
        return f"the command names {named} and begins with {_one_line(first)!r}, not the launcher; {single}"
    subcommand = _subcommand(words)
    if subcommand in ("python", "export"):
        return (
            f"the command names {named} through the launcher's {subcommand} sub-command, which "
            "runs code or writes a file where the agent says; the allowed form is a single launcher "
            f"command of another kind, e.g. {policy.launcher} commit @.assay/model_plan.json"
        )
    return None


# --- the entry point --------------------------------------------------------------


def decide(policy: Policy, tool: Any, tool_input: dict[str, Any], base: str, cwd: str) -> str | None:
    """The refusal for one tool call, or None to allow it."""
    if tool in EDITORS:
        raw = tool_input.get("file_path")
        if not isinstance(raw, str) or not raw:
            raw = tool_input.get("notebook_path")
        return editor_refusal(policy, str(tool), raw if isinstance(raw, str) else None, base)
    if tool == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return None
        return bash_refusal(policy, command, cwd)
    return None


def _refuse(reason: str) -> int:
    sys.stderr.write(REFUSED + _one_line(reason) + "\n")
    sys.stderr.flush()
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pre_tool_use.py",
        description="the PreToolUse hook of an ASSAY run directory; the hook JSON on stdin",
    )
    parser.add_argument("--policy", required=True, metavar="FILE", help="the policy file hooks install wrote")
    args = parser.parse_args(argv)
    policy, problem = load_policy(args.policy)
    if policy is None:
        return _refuse(
            f"the hook policy file {args.policy} {problem}; the operator reinstalls the hooks "
            f"with `assay hooks install --policy {args.policy}`"
        )
    try:
        payload = json.loads(sys.stdin.read() or "null")
    except ValueError as error:
        return _refuse(f"the hook input is not JSON ({error}); the tool call is refused until Claude Code sends the hook event as one JSON object")
    if not isinstance(payload, dict):
        return _refuse("the hook input is not a JSON object; the tool call is refused until Claude Code sends the hook event as one JSON object")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()
    base = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    reason = decide(policy, payload.get("tool_name"), tool_input, base, cwd)
    if reason is None:
        return 0
    return _refuse(reason)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""The PreToolUse hook of a Claude Code session on an ASSAY run directory
(docs/ARCHITECTURE.md section 8.4): the operator's tool policy, refused
before the tool runs.

`assay hooks install --policy FILE` writes the policy into FILE, outside the
run directory (the run directory, the anchor directory, the owner token file,
FILE's own path, the pinned launcher, the deny patterns, the interpreter), and
the hook entries into `<run dir>/.claude/settings.json`, each carrying FILE's
path. This script reads the hook's JSON on stdin and the policy from FILE, and
nothing the agent can write. It runs under the interpreter pinned at install,
in Claude Code's own process environment, with the standard library alone: it
imports nothing from `assay`, so it works whatever the agent did to its shell
or to the packages on its path.

The rules, in the order they are applied:

- `Write`, `Edit`, `MultiEdit`, `NotebookEdit`: the file path, resolved
  against `CLAUDE_PROJECT_DIR` (then the hook's `cwd`) and through realpath,
  is refused when it has the component `.assay` or `.claude` under the run
  directory (except `.assay/NOTES.md`, the agent's file), lies under the
  anchor directory, or is the owner token file or the policy file.
- `Bash`: a command that assigns, exports, unsets or aliases `ASSAY` or
  `PATH`, or defines a function named `assay`, is refused. A command matching
  a deny pattern (the operator's regular expressions: the world's client
  libraries, hosts and commands) is refused. A command that names a refused
  path, as the path component `.assay` or `.claude` in any word (a glob that
  would expand to it counts), or as a word that resolves under the anchor
  directory or to the token or policy file, is refused unless it is a single
  simple command whose first word is the pinned launcher, or `"$ASSAY"` when
  the policy pins the value the operator exported: a compound command (`;`,
  `&&`, `||`, `|`, a newline, `$(` or a backtick), a redirection, or a
  command shlex cannot parse is refused whatever it begins with.

A refusal is one line on stderr, `HOOK | REFUSED | <rule>; <allowed form>`,
with exit status 2, which Claude Code shows to the agent as the reason the
call was denied; everything else is exit status 0 with no output. The policy
is a text match over the command: the operator's tool policy made explicit,
not a sandbox.
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

POLICY_VERSION = 1
REFUSED = "HOOK | REFUSED | "
EDITORS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
STATE = ".assay"
SETTINGS = ".claude"
NOTES = "NOTES.md"
# The characters that make a command compound (section 8.4), over the raw
# text: `;`, `&&`, `||`, `|`, a newline, `$(` and a backtick.
COMPOUND = re.compile(r"[;|\n`]|&&|\$\(")
# A word that is a redirection, after shlex: `>`, `>>`, `2>`, `<`, `&>`,
# `>file`, `2>&1`.
REDIRECTION = re.compile(r"^(\d*[<>]|&>)")
# A change to the shell state the trust in the launcher rests on: an
# assignment of ASSAY or PATH (bare, exported, `env`, `declare -x`, `+=`,
# `${ASSAY:=...}`), an export, unset, declare, typeset, readonly, local or
# read naming them, an alias of assay, and a function named assay.
SHELL_STATE = re.compile(
    r"(?<![\w$])(?:ASSAY|PATH)(?:\[[^\]]*\])?[+:]?="
    r"|\$\{(?:ASSAY|PATH):?="
    r"|\b(?:export|unset|declare|typeset|readonly|local|read)\b[^;&|\n]*?(?<![\w$])(?:ASSAY|PATH)\b"
    r"|\balias\s+assay\b"
    r"|\bfunction\s+assay\b"
    r"|(?<![\w./-])assay\s*\(\s*\)"
)
GLOB = frozenset("*?[")


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
        deny = value.get("deny")
        if not isinstance(deny, list) or not all(isinstance(item, str) for item in deny):
            raise ValueError("deny must be a list of strings")
        policy = Policy(
            run_dir=_string(value, "run_dir"),
            anchor_dir=_string(value, "anchor_dir"),
            token_file=_optional_string(value, "token_file"),
            policy_file=_string(value, "policy_file"),
            launcher=_string(value, "launcher"),
            assay_value=_optional_string(value, "assay_value"),
            deny=tuple(deny),
        )
    except ValueError as error:
        return None, f"is not a policy file ({error})"
    return policy, ""


# --- paths --------------------------------------------------------------------


def _one_line(text: str) -> str:
    return text.replace("\r", "\\r").replace("\n", "\\n")


def _under(path: str, directory: str) -> bool:
    """Whether `path` is `directory` or lies under it (both resolved)."""
    return path == directory or path.startswith(directory.rstrip(os.sep) + os.sep)


def _resolve(raw: str, base: str) -> str:
    expanded = os.path.expandvars(os.path.expanduser(raw))
    return os.path.realpath(os.path.join(base, expanded))


def _components_under_run(policy: Policy, resolved: str) -> list[str]:
    """The path's components below the run directory, empty outside it."""
    if not _under(resolved, policy.run_dir):
        return []
    relative = resolved[len(policy.run_dir.rstrip(os.sep)) :].strip(os.sep)
    return [part for part in relative.split(os.sep) if part]


def _protected_outside_run(policy: Policy, resolved: str) -> str | None:
    """What a resolved path is, when it is one of the operator's: under the
    anchor directory, the owner token file, the policy file; else None."""
    if _under(resolved, policy.anchor_dir):
        return "the anchor directory"
    if policy.token_file is not None and resolved == policy.token_file:
        return "the owner token file"
    if resolved == policy.policy_file:
        return "the hook policy file"
    return None


ELSEWHERE = "your files go in the run directory outside .assay and .claude"


def editor_refusal(policy: Policy, raw: str, base: str) -> str | None:
    """The refusal of a Write, Edit, MultiEdit or NotebookEdit, or None."""
    resolved = _resolve(raw, base)
    shown = _one_line(raw)
    if resolved == os.path.join(policy.run_dir, STATE, NOTES):
        return None
    parts = _components_under_run(policy, resolved)
    if STATE in parts:
        return (
            f"{shown} lies under .assay, which the daemon writes; .assay/NOTES.md is the "
            "one file there that is yours, the rest is reached through assay"
        )
    if SETTINGS in parts:
        return f"{shown} lies under .claude, the operator's settings; nothing there is yours to write, {ELSEWHERE}"
    what = _protected_outside_run(policy, resolved)
    if what == "the anchor directory":
        return f"{shown} lies under the anchor directory, the operator's record of the chain heads; nothing there is yours to write, {ELSEWHERE}"
    if what is not None:
        return f"{shown} is {what}, which the operator holds; nothing there is yours to write, {ELSEWHERE}"
    return None


# --- the Bash command -------------------------------------------------------------


def _words_of(command: str) -> list[str]:
    """The words a command text names, by two readings: the raw text split
    on whitespace, the shell's operators and quotes (so a word inside a
    quoted string or beside an operator is still seen), and the shell's own
    reading through shlex when it parses (so `".ass""ay"` is `.assay`)."""
    words = [token for token in re.split(r"[\s;&|()<>'\"`]+", command) if token]
    try:
        words.extend(shlex.split(command))
    except ValueError:
        pass
    return words


def _path_of_word(word: str) -> str:
    """The path a word may name: the value of a `KEY=VALUE` word, without
    the `@` of the command line's `@FILE` form."""
    key, separator, value = word.partition("=")
    if separator and "/" not in key:
        word = value
    return word[1:] if word.startswith("@") else word


def _names_component(word: str, name: str) -> bool:
    """Whether the word has `name` as a path component, or a glob component
    that would expand to it (a leading dot must be explicit, as in the shell)."""
    for component in word.split("/"):
        if component == name:
            return True
        if component.startswith(".") and GLOB & set(component) and fnmatch.fnmatchcase(name, component):
            return True
    return False


def named_path(policy: Policy, command: str, cwd: str) -> str | None:
    """What refused path the command names, or None: `.assay` or `.claude`
    as a component of any word, or a word that resolves (relative names
    against the cwd) under the anchor directory or to the token or policy
    file."""
    for word in _words_of(command):
        candidate = _path_of_word(word)
        if not candidate:
            continue
        if _names_component(candidate, STATE):
            return ".assay (the run's state directory)"
        if _names_component(candidate, SETTINGS):
            return ".claude (the operator's settings)"
        what = _protected_outside_run(policy, _resolve(candidate, cwd))
        if what is not None:
            return what
    return None


def _launcher_forms(policy: Policy) -> str:
    if policy.assay_value is not None:
        return f'{policy.launcher} (or "$ASSAY", the launcher the operator exported)'
    return policy.launcher


def bash_refusal(policy: Policy, command: str, cwd: str) -> str | None:
    """The refusal of a Bash command, or None."""
    if SHELL_STATE.search(command):
        return (
            "the command assigns, exports, unsets or aliases ASSAY or PATH, or defines a "
            f"function named assay; the launcher is {_launcher_forms(policy)}, run it by that "
            "path and leave the shell as it is"
        )
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
    named = named_path(policy, command, cwd)
    if named is None:
        return None
    single = (
        f"the allowed form is a single {_launcher_forms(policy)} command, e.g. "
        f"{policy.launcher} commit @.assay/model_plan.json"
    )
    if COMPOUND.search(command):
        return (
            f"the command names {named} inside a compound command (;, &&, ||, |, a newline, "
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
    if first == policy.launcher:
        return None
    if policy.assay_value is not None and first in ("$ASSAY", "${ASSAY}"):
        return None
    return f"the command names {named} and begins with {_one_line(first)!r}, not the launcher; {single}"


# --- the entry point --------------------------------------------------------------


def decide(policy: Policy, tool: Any, tool_input: dict[str, Any], base: str, cwd: str) -> str | None:
    """The refusal for one tool call, or None to allow it."""
    if tool in EDITORS:
        raw = tool_input.get("file_path")
        if not isinstance(raw, str):
            raw = tool_input.get("notebook_path")
        if not isinstance(raw, str) or not raw:
            return None
        return editor_refusal(policy, raw, base)
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

"""The error catalogue (docs/ARCHITECTURE.md section 7.1): every code the
kernel raises, with its kind and a one-line meaning.

A refusal is an `AssayError` (`core.py`) carrying a code from this table. The
code is UPPER_SNAKE, unique and stable from the 1.2.0 tag on, like the receipt
result tokens. 1.3.0 renamed one code, the code of a prediction that does not
parse. The changelog names both spellings. The kind decides the exit code of
the command line and rides on the socket and in `--json`: `usage` (the request is wrong; exit 2),
`refused` (a well-formed request the kernel refuses by rule, or the harness's
own state refuses; exit 2), `world` (the adapter or the world failed or
refused at the kernel boundary; exit 3), `internal` (a bug or a corrupt file;
exit 4) and `invalid` (the run can no longer be scored or continued; exit 5).
The catalogue's kind wins for a catalogued code; the `kind` keyword of a raise
applies to any code outside the table, which is where `UNSPECIFIED` lives, the
code of a raise without one (an adapter's or a module's).

`tests/test_errors.py` walks the AST of `src/assay` and `src/assay_grid` and
refuses a raise of `AssayError` without `code=`, or with a code that is not a
string literal or an `errors.NAME` attribute in this table. The table renders
into `docs/ERRORS.md` with `python -m assay.errors --render`, and the same
test asserts the file is current. The module imports nothing from the package,
so `core.py` can read it.
"""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Mapping

KINDS: tuple[str, ...] = ("usage", "refused", "world", "internal", "invalid")

EXIT_CODES: Mapping[str, int] = {
    "usage": 2,
    "refused": 2,
    "world": 3,
    "internal": 4,
    "invalid": 5,
}

# The code of a raise without one: outside the table, with the kind the raise
# asks for.
UNSPECIFIED = "UNSPECIFIED"

# --- the codes --------------------------------------------------------------------

# usage: the request is wrong
CLI_USAGE = "CLI_USAGE"
COMMAND_ARGS = "COMMAND_ARGS"
RUN_MISSING = "RUN_MISSING"
WORLD_ID_INVALID = "WORLD_ID_INVALID"
ADAPTER_SPEC = "ADAPTER_SPEC"
REGISTRY_INVALID = "REGISTRY_INVALID"
REGISTRY_MISSING = "REGISTRY_MISSING"
ACTION_UNKNOWN = "ACTION_UNKNOWN"
ACTION_PARAMS = "ACTION_PARAMS"
PREDICTION_SYNTAX = "PREDICTION_SYNTAX"
STATE_UNKNOWN = "STATE_UNKNOWN"
STATE_DECLARE = "STATE_DECLARE"
FILE_NOT_FOUND = "FILE_NOT_FOUND"
PATH_INVALID = "PATH_INVALID"
MODULE_CONTRACT = "MODULE_CONTRACT"
MODEL_INVALID = "MODEL_INVALID"
MODEL_FAILED = "MODEL_FAILED"
PYTHON_FAILED = "PYTHON_FAILED"
PLAN_INVALID = "PLAN_INVALID"
KNOWLEDGE_INVALID = "KNOWLEDGE_INVALID"
EXTRA_MISSING = "EXTRA_MISSING"
RESUME_REFUSED = "RESUME_REFUSED"
ANCHOR_DIR_UNWRITABLE = "ANCHOR_DIR_UNWRITABLE"
REQUEST_MALFORMED = "REQUEST_MALFORMED"
OPERATION_UNKNOWN = "OPERATION_UNKNOWN"
PROTOCOL_VERSION = "PROTOCOL_VERSION"
HOOK_POLICY_INVALID = "HOOK_POLICY_INVALID"
HOOK_INPUT_MALFORMED = "HOOK_INPUT_MALFORMED"
HOOK_CHECK_FAILED = "HOOK_CHECK_FAILED"

# refused: a well-formed request the kernel refuses by rule, or the harness's
# own state refuses
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
PREDICTION_REQUIRED = "PREDICTION_REQUIRED"
GATE_OFF = "GATE_OFF"
EVENT_GUARD = "EVENT_GUARD"
RUN_COMPLETE = "RUN_COMPLETE"
ACTION_UNAVAILABLE = "ACTION_UNAVAILABLE"
BATCH_FORBIDDEN = "BATCH_FORBIDDEN"
BATCH_CAP = "BATCH_CAP"
BATCHING_RIGHTS = "BATCHING_RIGHTS"
PLAN_STALE = "PLAN_STALE"
DESTRUCTIVE_UNDECLARED = "DESTRUCTIVE_UNDECLARED"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
REHEARSAL_REQUIRED = "REHEARSAL_REQUIRED"
OWNER_TOKEN = "OWNER_TOKEN"
DAEMON_TOKEN = "DAEMON_TOKEN"
MODULE_DEMAND = "MODULE_DEMAND"
NOTES_CAP = "NOTES_CAP"
RESET_REASON = "RESET_REASON"
GOAL_PROPOSAL = "GOAL_PROPOSAL"
STATE_CAP = "STATE_CAP"
STATE_REDEFINED = "STATE_REDEFINED"
DAEMON_UNAVAILABLE = "DAEMON_UNAVAILABLE"
DAEMON_BUSY = "DAEMON_BUSY"
DAEMON_ORPHANED = "DAEMON_ORPHANED"
DECLARATION_CHANGED = "DECLARATION_CHANGED"
ANCHOR_FILE_MISMATCH = "ANCHOR_FILE_MISMATCH"

# world: the adapter or the world failed or refused at the kernel boundary
WORLD_ERROR = "WORLD_ERROR"
OBSERVATION_INVALID = "OBSERVATION_INVALID"

# internal: a bug or a corrupt file
INTERNAL = "INTERNAL"
RECORD_CORRUPT = "RECORD_CORRUPT"
REPLY_MALFORMED = "REPLY_MALFORMED"
TIMELINE_EMPTY = "TIMELINE_EMPTY"

# invalid: the run can no longer be scored or continued
CHAIN_DIVERGED = "CHAIN_DIVERGED"
RUN_SEALED = "RUN_SEALED"
TAMPER_DETECTED = "TAMPER_DETECTED"
LOCAL_REPLAY_DIVERGED = "LOCAL_REPLAY_DIVERGED"
REMOTE_LEASE_EXPIRED = "REMOTE_LEASE_EXPIRED"
REMOTE_STATE_DIVERGED = "REMOTE_STATE_DIVERGED"
REMOTE_SESSION_UNAVAILABLE = "REMOTE_SESSION_UNAVAILABLE"


@dataclasses.dataclass(frozen=True, slots=True)
class ErrorCode:
    """One row of the catalogue."""

    code: str
    kind: str
    meaning: str


CATALOGUE: tuple[ErrorCode, ...] = (
    # usage
    ErrorCode(CLI_USAGE, "usage", "the command line does not parse, caught by argparse: an unknown command or flag, a missing argument, a value of the wrong type; COMMAND_ARGS is the same situation caught by the command itself."),
    ErrorCode(COMMAND_ARGS, "usage", "the arguments do not fit the command, caught by the command itself after the parse: a missing or conflicting flag, a malformed value, a target already there; CLI_USAGE is the same situation caught by argparse."),
    ErrorCode(RUN_MISSING, "usage", "the directory owns no run."),
    ErrorCode(WORLD_ID_INVALID, "usage", "the world id breaks the rule: non-empty, up to 64 characters, no whitespace, control characters or path separators."),
    ErrorCode(ADAPTER_SPEC, "usage", "the adapter spec cannot be resolved, imported or called as `module:factory` or `/path/file.py:factory`."),
    ErrorCode(REGISTRY_INVALID, "usage", "the registry file is missing, is not JSON or breaks the registry schema."),
    ErrorCode(REGISTRY_MISSING, "usage", "the run has no pinned registry (it started before 1.2.0): it can be inspected, not resumed, and nothing acts on it."),
    ErrorCode(ACTION_UNKNOWN, "usage", "the action is not registered."),
    ErrorCode(ACTION_PARAMS, "usage", "the parameters do not fit the registered schema: one missing, unknown or repeated, a wrong type, bound, enum, length or item count, nested values included, an object or an array as a token, or a broken `pname=value`."),
    ErrorCode(PREDICTION_SYNTAX, "usage", "a prediction does not parse: a malformed outcome, a form of another observation kind, a bad window or aggregate bound."),
    ErrorCode(STATE_UNKNOWN, "usage", "an outcome or a model names an addressable state that is not registered."),
    ErrorCode(STATE_DECLARE, "usage", "a state declaration is malformed: the name, a host state, neither or both of --path and --file, an empty path."),
    ErrorCode(FILE_NOT_FOUND, "usage", "a file the command names does not exist: a verifier, an extractor, a module."),
    ErrorCode(PATH_INVALID, "usage", "a path lies outside the run directory, inside `.assay`, or is absolute where a relative one is required."),
    ErrorCode(MODULE_CONTRACT, "usage", "a module file does not import, lacks the module contract, is not one `.py` file, or names a built-in or already-provided NAME."),
    ErrorCode(MODEL_INVALID, "usage", "`model.py` is missing or declares no STATES, or the solve goal names a state it does not declare."),
    ErrorCode(MODEL_FAILED, "usage", "the agent's `model.py` raised, timed out or produced malformed output in the sandbox."),
    ErrorCode(PYTHON_FAILED, "usage", "the agent's source raised inside `assay python`."),
    ErrorCode(PLAN_INVALID, "usage", "the commit plan is not a model plan written by `assay model solve`, is not `.assay/model_plan.json`, or carries its actions in the string form of a plan written before 1.2.0."),
    ErrorCode(KNOWLEDGE_INVALID, "usage", "the knowledge file cannot be read, is not JSON or has another format."),
    ErrorCode(EXTRA_MISSING, "usage", "an optional extra the command needs is not importable: the frame-world extra (assay_grid) on a run with frame observations, or the mcp package (the server extra) under `assay serve-tools`."),
    ErrorCode(RESUME_REFUSED, "usage", "start's arguments disagree with the directory's run: another world id, mode or registry, or an import on resume."),
    ErrorCode(ANCHOR_DIR_UNWRITABLE, "usage", "the anchor directory cannot be written, so the run's chain heads and its seal would have nowhere to go; start refuses before anything is written."),
    ErrorCode(REQUEST_MALFORMED, "usage", "the request line on the socket is empty, longer than 1,000,000 bytes, not JSON, not a JSON object, repeats a key, nests more than 64 containers deep, or its body does not fit the operation's request record."),
    ErrorCode(OPERATION_UNKNOWN, "usage", "the daemon operation is not in the wire table (the retired `step` included); nothing is written."),
    ErrorCode(PROTOCOL_VERSION, "usage", "the request or the reply carries no `v`, or another version than this package speaks."),
    ErrorCode(HOOK_POLICY_INVALID, "usage", "the hook policy file `hooks install` wrote cannot be read, is not JSON, or has another shape or version; the hooks are reinstalled from the operator's shell."),
    ErrorCode(HOOK_INPUT_MALFORMED, "usage", "the hook JSON on stdin is empty, not a JSON object, or lacks the fields a tool event carries (`tool_name`, `tool_input`, `tool_use_id`, `session_id`)."),
    ErrorCode(HOOK_CHECK_FAILED, "usage", "the installed hooks cannot run: no harness entry in the settings, or the pinned interpreter, the script or the launcher is missing or refuses a benign event; a hook that cannot start fails open."),
    # refused
    ErrorCode(BUDGET_EXHAUSTED, "refused", "the action budget or the reported spend cap is reached; act, commit and reset are refused."),
    ErrorCode(PREDICTION_REQUIRED, "refused", "the gate: a paid action or a batch step came without a prediction."),
    ErrorCode(GATE_OFF, "refused", "the registry's gate is off and a prediction was supplied; nothing is graded on this run."),
    ErrorCode(EVENT_GUARD, "refused", "the event guard (--at) names another event than the current one."),
    ErrorCode(RUN_COMPLETE, "refused", "the goal is reached; the run takes no more paid actions."),
    ErrorCode(ACTION_UNAVAILABLE, "refused", "the affordance check: the observation does not advertise the action."),
    ErrorCode(BATCH_FORBIDDEN, "refused", "a reset, a destructive action or an approval-gated action cannot hide inside a batch."),
    ErrorCode(BATCH_CAP, "refused", "the batching law caps hand-written batches; longer batches belong to a replay-fit model plan."),
    ErrorCode(BATCHING_RIGHTS, "refused", "a model plan needs replay-fit batching rights on this journal."),
    ErrorCode(PLAN_STALE, "refused", "the model plan predates a change of `model.py` or of the journal."),
    ErrorCode(DESTRUCTIVE_UNDECLARED, "refused", "a destructive action refuses without a declared worst case and recovery plan."),
    ErrorCode(APPROVAL_REQUIRED, "refused", "an approval-gated action needs a fresh owner approval."),
    ErrorCode(REHEARSAL_REQUIRED, "refused", "a live actuator needs its rehearsal quota met, or an owner waiver."),
    ErrorCode(OWNER_TOKEN, "refused", "the owner token is missing or wrong, or none was minted for this run."),
    ErrorCode(DAEMON_TOKEN, "refused", "the daemon token on the request is wrong."),
    ErrorCode(MODULE_DEMAND, "refused", "a block-mode module demands a declaration before the action; declaring unlocks it."),
    ErrorCode(NOTES_CAP, "refused", "the notes file is past twice its cap; paid actions refuse until it is trimmed."),
    ErrorCode(RESET_REASON, "refused", "a reset needs --because unless the state is GAME_OVER."),
    ErrorCode(GOAL_PROPOSAL, "refused", "the goal proposal does not exist or is already resolved."),
    ErrorCode(STATE_CAP, "refused", "the declared states are at the per-run cap."),
    ErrorCode(STATE_REDEFINED, "refused", "the state is already declared with a different extractor."),
    ErrorCode(DAEMON_UNAVAILABLE, "refused", "the harness's own state refused: the run's environment owner (the daemon) is not running or did not start."),
    ErrorCode(DAEMON_BUSY, "refused", "the harness's own state refused: the daemon is alive and inside a step, hung, or did not answer within the client's wait."),
    ErrorCode(DAEMON_ORPHANED, "refused", "the harness's own state refused: a live daemon still serves this directory while its run state is gone."),
    ErrorCode(DECLARATION_CHANGED, "refused", "the adapter's session declaration differs from the one recorded at the run's start; the run continues only under the recorded one."),
    ErrorCode(ANCHOR_FILE_MISMATCH, "refused", "the anchor file recorded in config.json is not the one the environment names (ASSAY_ANCHOR_DIR and the run's digest); the daemon does not start over a moved anchor file."),
    # world
    ErrorCode(WORLD_ERROR, "world", "the world raised or refused inside the adapter's factory, observation or step; nothing was journaled for the action."),
    ErrorCode(OBSERVATION_INVALID, "world", "the adapter's observation has a shape the kernel does not take: no observation, not a JSON object under `data`, no frames, a frame that is not 2-D or has colors outside 0..15."),
    # internal
    ErrorCode(INTERNAL, "internal", "a bug: an exception that is not a refusal; the traceback is saved."),
    ErrorCode(RECORD_CORRUPT, "internal", "a stored file does not decode: corrupt JSON, a bad frame encoding."),
    ErrorCode(REPLY_MALFORMED, "internal", "the daemon's reply is empty, not JSON, not an object, or carries no result."),
    ErrorCode(TIMELINE_EMPTY, "internal", "the run has no events; event 0 is the daemon's before READY, so an empty journal is a broken run."),
    # invalid
    ErrorCode(CHAIN_DIVERGED, "invalid", "the journal, its chain file or its contiguity no longer agree; the run is refused and nothing is rewritten."),
    ErrorCode(RUN_SEALED, "invalid", "the anchor file carries a seal: the daemon found the run's files changed under it and ended the record there; the run is refused until the operator removes the sealing line."),
    ErrorCode(TAMPER_DETECTED, "invalid", "a file changed under the daemon, which keeps the record it holds and refuses every paid action until it is stopped."),
    ErrorCode(LOCAL_REPLAY_DIVERGED, "invalid", "the local world no longer reproduces the journal on replay."),
    ErrorCode(REMOTE_LEASE_EXPIRED, "invalid", "the remote session's action-idle lease has run out; the run is not recoverable."),
    ErrorCode(REMOTE_STATE_DIVERGED, "invalid", "the live remote observation differs from the journal."),
    ErrorCode(REMOTE_SESSION_UNAVAILABLE, "invalid", "the session of a world that declared no replay returned nothing or is gone; the run cannot be reconstructed."),
)

BY_CODE: Mapping[str, ErrorCode] = {entry.code: entry for entry in CATALOGUE}


def kind_of(code: str, requested: str) -> str:
    """The kind an error of this code has: the catalogue's for a catalogued
    code, the raise's own for any code outside the table (`UNSPECIFIED`
    among them)."""
    entry = BY_CODE.get(code)
    if entry is not None:
        return entry.kind
    return requested if requested in KINDS else "refused"


def exit_code(kind: str) -> int:
    """The command line's exit status for an error of this kind."""
    return EXIT_CODES.get(kind, EXIT_CODES["refused"])


def render() -> str:
    """`docs/ERRORS.md`, rendered from the table."""
    lines = [
        "# ASSAY error codes",
        "",
        "Rendered from `src/assay/errors.py` by `python -m assay.errors --render`;",
        "`tests/test_errors.py` asserts this file is current. Every refusal the",
        "harness prints or sends carries one of these codes: on the command line",
        "as `ERROR | CODE | message` on stderr, with a second line `NEXT | hint`",
        "when the error names a next step, then any detail the error carries (the",
        "grammar table), and the exit status of the kind; over the socket and under",
        "`--json` as the object `{\"code\", \"kind\", \"message\", \"hint\",",
        "\"detail\"}` (docs/ARCHITECTURE.md section 7.1).",
        "",
        "## The kinds and their exit codes",
        "",
        "| kind | exit | meaning |",
        "| --- | --- | --- |",
        "| `usage` | 2 | the request is wrong: schema, syntax, a missing file, an unknown action |",
        "| `refused` | 2 | a well-formed request the kernel refuses by rule (the budget, the gates, a module demand, the batching law, the affordance check, the notes cap), or the harness's own state refuses (the daemon) |",
        "| `world` | 3 | the adapter or the world failed or refused at the kernel boundary |",
        "| `internal` | 4 | a bug or a corrupt file; the traceback is saved |",
        "| `invalid` | 5 | the run can no longer be scored or continued: tamper, chain or replay divergence, remote divergence |",
        "",
        "The catalogue's kind wins for a catalogued code; the `kind` keyword of a",
        "raise applies to any code outside the table. An adapter or a module that",
        "raises `AssayError` without a code raises `UNSPECIFIED`, outside the",
        "table, with the kind the raise asks for (`refused` by default); an error",
        "the daemon meets inside the adapter's factory, observation or step becomes",
        "`WORLD_ERROR`. The codes froze at the 1.2.0 tag. 1.3.0 renamed one code, the",
        "code of a prediction that does not parse. The changelog names both spellings.",
        "",
        "## The codes",
        "",
        "| code | kind | meaning |",
        "| --- | --- | --- |",
    ]
    for entry in CATALOGUE:
        lines.append(f"| `{entry.code}` | {entry.kind} | {entry.meaning} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--render"]:
        sys.stdout.write(render())
        return 0
    sys.stderr.write("usage: python -m assay.errors --render\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())

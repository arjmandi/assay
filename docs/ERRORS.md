# ASSAY error codes

Rendered from `src/assay/errors.py` by `python -m assay.errors --render`;
`tests/test_errors.py` asserts this file is current. Every refusal the
harness prints or sends carries one of these codes: on the command line
as `ERROR | CODE | message` on stderr, with a second line `NEXT | hint`
when the error names a next step, then any detail the error carries (the
claims table), and the exit status of the kind; over the socket and under
`--json` as the object `{"code", "kind", "message", "hint",
"detail"}` (docs/ARCHITECTURE.md section 7.1).

## The kinds and their exit codes

| kind | exit | meaning |
| --- | --- | --- |
| `usage` | 2 | the request is wrong: schema, syntax, a missing file, an unknown action |
| `refused` | 2 | a well-formed request the kernel refuses by rule (the budget, the gates, a module demand, the batching law, the affordance check, the notes cap), or the harness's own state refuses (the daemon) |
| `world` | 3 | the adapter or the world failed or refused at the kernel boundary |
| `internal` | 4 | a bug or a corrupt file; the traceback is saved |
| `invalid` | 5 | the run can no longer be scored or continued: tamper, chain or replay divergence, remote divergence |

The catalogue's kind wins for a catalogued code; the `kind` keyword of a
raise applies to any code outside the table. An adapter or a module that
raises `AssayError` without a code raises `UNSPECIFIED`, outside the
table, with the kind the raise asks for (`refused` by default); an error
the daemon meets inside the adapter's factory, observation or step becomes
`WORLD_ERROR`. The codes freeze at the 1.2.0 tag.

## The codes

| code | kind | meaning |
| --- | --- | --- |
| `CLI_USAGE` | usage | the command line does not parse, caught by argparse: an unknown command or flag, a missing argument, a value of the wrong type; COMMAND_ARGS is the same situation caught by the command itself. |
| `COMMAND_ARGS` | usage | the arguments do not fit the command, caught by the command itself after the parse: a missing or conflicting flag, a malformed value, a target already there; CLI_USAGE is the same situation caught by argparse. |
| `RUN_MISSING` | usage | the directory owns no run. |
| `WORLD_ID_INVALID` | usage | the world id breaks the rule: non-empty, up to 64 characters, no whitespace, control characters or path separators. |
| `ADAPTER_SPEC` | usage | the adapter spec cannot be resolved, imported or called as `module:factory` or `/path/file.py:factory`. |
| `REGISTRY_INVALID` | usage | the registry file is missing, is not JSON or breaks the registry schema. |
| `REGISTRY_MISSING` | usage | the run has no pinned registry (it started before 1.2.0): it can be inspected, not resumed, and nothing acts on it. |
| `ACTION_UNKNOWN` | usage | the action is not registered. |
| `ACTION_PARAMS` | usage | the parameters do not fit the registered schema: one missing, unknown or repeated, a wrong type, bound, enum, length or item count, nested values included, an object or an array as a token, or a broken `pname=value`. |
| `CLAIM_SYNTAX` | usage | a prediction does not parse: a malformed claim, a form of another observation kind, a bad window or aggregate bound. |
| `CHANNEL_UNKNOWN` | usage | a claim or a model names a channel that is not registered. |
| `CHANNEL_DECLARE` | usage | a channel declaration is malformed: the name, a host channel, neither or both of --path and --file, an empty path. |
| `FILE_NOT_FOUND` | usage | a file the command names does not exist: a verifier, an extractor, a module. |
| `PATH_INVALID` | usage | a path lies outside the run directory, inside `.assay`, or is absolute where a relative one is required. |
| `MODULE_CONTRACT` | usage | a module file does not import, lacks the module contract, is not one `.py` file, or names a built-in or already-provided NAME. |
| `MODEL_INVALID` | usage | `model.py` is missing or declares no CHANNELS, or the solve goal names a channel it does not declare. |
| `MODEL_FAILED` | usage | the agent's `model.py` raised, timed out or produced malformed output in the sandbox. |
| `PYTHON_FAILED` | usage | the agent's source raised inside `assay python`. |
| `PLAN_INVALID` | usage | the commit plan is not a model plan written by `assay model solve`, is not `.assay/model_plan.json`, or carries its actions in the string form of a plan written before 1.2.0. |
| `KNOWLEDGE_INVALID` | usage | the knowledge file cannot be read, is not JSON or has another format. |
| `EXTRA_MISSING` | usage | the run has frame observations and the frame-world extra (assay_grid) is not importable. |
| `RESUME_REFUSED` | usage | start's arguments disagree with the directory's run: another world id, mode or registry, or an import on resume. |
| `REQUEST_MALFORMED` | usage | the request line on the socket is empty, not a JSON object, or its body does not fit the operation's request record. |
| `OPERATION_UNKNOWN` | usage | the daemon operation is not in the wire table (the retired `step` included); nothing is written. |
| `PROTOCOL_VERSION` | usage | the request or the reply carries no `v`, or another version than this package speaks. |
| `BUDGET_EXHAUSTED` | refused | the action budget or the reported spend cap is reached; act, commit and reset are refused. |
| `PREDICTION_REQUIRED` | refused | the gate: a paid action or a batch step came without a prediction. |
| `GATE_OFF` | refused | the registry's gate is off and a prediction was supplied; nothing is graded on this run. |
| `EVENT_GUARD` | refused | the event guard (--at) names another event than the current one. |
| `RUN_COMPLETE` | refused | the goal is reached; the run takes no more paid actions. |
| `ACTION_UNAVAILABLE` | refused | the affordance check: the observation does not advertise the action. |
| `BATCH_FORBIDDEN` | refused | a reset, a destructive action or an approval-gated action cannot hide inside a batch. |
| `BATCH_CAP` | refused | the batching law caps hand-written batches; longer batches belong to a replay-fit model plan. |
| `BATCHING_RIGHTS` | refused | a model plan needs replay-fit batching rights on this journal. |
| `PLAN_STALE` | refused | the model plan predates a change of `model.py` or of the journal. |
| `DESTRUCTIVE_UNDECLARED` | refused | a destructive action refuses without a declared worst case and recovery plan. |
| `APPROVAL_REQUIRED` | refused | an approval-gated action needs a fresh owner approval. |
| `REHEARSAL_REQUIRED` | refused | a live actuator needs its rehearsal quota met, or an owner waiver. |
| `OWNER_TOKEN` | refused | the owner token is missing or wrong, or none was minted for this run. |
| `DAEMON_TOKEN` | refused | the daemon token on the request is wrong. |
| `MODULE_DEMAND` | refused | a block-mode module demands a declaration before the action; declaring unlocks it. |
| `NOTES_CAP` | refused | the notes file is past twice its cap; paid actions refuse until it is trimmed. |
| `RESET_REASON` | refused | a reset needs --because unless the state is GAME_OVER. |
| `GOAL_PROPOSAL` | refused | the goal proposal does not exist or is already resolved. |
| `CHANNEL_CAP` | refused | the declared channels are at the per-run cap. |
| `CHANNEL_REDEFINED` | refused | the channel is already declared with a different extractor. |
| `DAEMON_UNAVAILABLE` | refused | the harness's own state refused: the run's environment owner (the daemon) is not running or did not start. |
| `DAEMON_BUSY` | refused | the harness's own state refused: the daemon is alive and inside a step, hung, or did not answer within the client's wait. |
| `DAEMON_ORPHANED` | refused | the harness's own state refused: a live daemon still serves this directory while its run state is gone. |
| `DECLARATION_CHANGED` | refused | the adapter's session declaration differs from the one recorded at the run's start; the run continues only under the recorded one. |
| `WORLD_ERROR` | world | the world raised or refused inside the adapter's factory, observation or step; nothing was journaled for the action. |
| `OBSERVATION_INVALID` | world | the adapter's observation has a shape the kernel does not take: no observation, not a JSON object under `data`, no frames, a frame that is not 2-D or has colors outside 0..15. |
| `INTERNAL` | internal | a bug: an exception that is not a refusal; the traceback is saved. |
| `RECORD_CORRUPT` | internal | a stored file does not decode: corrupt JSON, a bad frame encoding. |
| `REPLY_MALFORMED` | internal | the daemon's reply is empty, not JSON, not an object, or carries no result. |
| `TIMELINE_EMPTY` | internal | the run has no events; event 0 is the daemon's before READY, so an empty journal is a broken run. |
| `CHAIN_DIVERGED` | invalid | the journal, its chain file or its contiguity no longer agree; the run is refused and nothing is rewritten. |
| `RUN_SEALED` | invalid | the anchor file carries a seal: the daemon found the run's files changed under it and ended the record there; the run is refused until the operator removes the sealing line. |
| `TAMPER_DETECTED` | invalid | a file changed under the daemon, which keeps the record it holds and refuses every paid action until it is stopped. |
| `LOCAL_REPLAY_DIVERGED` | invalid | the local world no longer reproduces the journal on replay. |
| `REMOTE_LEASE_EXPIRED` | invalid | the remote session's action-idle lease has run out; the run is not recoverable. |
| `REMOTE_STATE_DIVERGED` | invalid | the live remote observation differs from the journal. |
| `REMOTE_SESSION_UNAVAILABLE` | invalid | the session of a world that declared no replay returned nothing or is gone; the run cannot be reconstructed. |

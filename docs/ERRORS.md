# ASSAY error codes

Rendered from `src/assay/errors.py` by `python -m assay.errors --render`;
`tests/test_errors.py` asserts this file is current. Every refusal the
harness prints or sends carries one of these codes: on the command line
as `ERROR | CODE | message` on stderr, with a second line `NEXT | hint`
when the error names a next step, and the exit status of the kind; over
the socket and under `--json` as the object `{"code", "kind",
"message", "hint"}` (docs/ARCHITECTURE.md section 7.1).

## The kinds and their exit codes

| kind | exit | meaning |
| --- | --- | --- |
| `usage` | 2 | the request is wrong: schema, syntax, a missing file, an unknown action |
| `refused` | 2 | a well-formed request the kernel refuses by rule: the budget, the gates, a module demand, the batching law, the affordance check, the notes cap |
| `world` | 3 | the adapter or the world failed or refused at the kernel boundary |
| `internal` | 4 | a bug or a corrupt file; the traceback is saved |
| `invalid` | 5 | the run can no longer be scored or continued: tamper, chain or replay divergence, remote divergence |

The catalogue's kind wins for a catalogued code. An adapter or a module
that raises `AssayError` without a code raises `UNSPECIFIED`, whose kind
is the raise's own (`refused` by default); an error the daemon meets
inside the adapter's factory, observation or step becomes `WORLD_ERROR`.
The codes freeze at the 1.2.0 tag.

## The codes

| code | kind | meaning |
| --- | --- | --- |
| `CLI_USAGE` | usage | the command line does not parse: an unknown command or flag, a missing argument, a value of the wrong type. |
| `COMMAND_ARGS` | usage | the arguments do not fit the command: a missing or conflicting flag, a malformed value, a target already there. |
| `RUN_MISSING` | usage | the directory owns no run; `assay start WORLD_ID` creates one. |
| `WORLD_ID_INVALID` | usage | the world id breaks the rule: non-empty, up to 64 characters, no whitespace, control characters or path separators. |
| `ADAPTER_SPEC` | usage | the adapter spec cannot be resolved, imported or called as `module:factory` or `/path/file.py:factory`. |
| `REGISTRY_INVALID` | usage | the registry file is missing, is not JSON or breaks the registry schema. |
| `REGISTRY_MISSING` | usage | the run has no pinned registry (it started before 1.2.0): it can be inspected, not resumed, and nothing acts on it. |
| `ACTION_UNKNOWN` | usage | the action is not registered. |
| `ACTION_PARAMS` | usage | the parameters do not fit the registered schema: one missing, unknown or repeated, a wrong type, out of bounds or outside the enum, or the `pname=value` form broken. |
| `CLAIM_SYNTAX` | usage | a prediction does not parse: a malformed claim, a form of another observation kind, a bad window or aggregate bound. |
| `CHANNEL_UNKNOWN` | usage | a claim or a model names a channel that is not registered. |
| `CHANNEL_DECLARE` | usage | a channel declaration is malformed: the name, a host channel, neither or both of --path and --file, an empty path. |
| `FILE_NOT_FOUND` | usage | a file the command names does not exist: a verifier, an extractor, a module, a knowledge file. |
| `PATH_INVALID` | usage | a path lies outside the run directory, inside `.assay`, or is absolute where a relative one is required. |
| `MODULE_CONTRACT` | usage | a module file does not import, lacks the module contract, is not one `.py` file, or names a built-in or already-provided NAME. |
| `MODEL_INVALID` | usage | `model.py` is missing or declares no CHANNELS, or the solve goal names a channel it does not declare. |
| `PLAN_INVALID` | usage | the commit plan is not a model plan written by `assay model solve`, or is not `.assay/model_plan.json`. |
| `KNOWLEDGE_INVALID` | usage | the knowledge file cannot be read, is not JSON or has another format. |
| `EXTRA_MISSING` | usage | the run has frame observations and the frame-world extra (assay_grid) is not importable. |
| `MALFORMED_REQUEST` | usage | the request line on the socket is empty or not a JSON object. |
| `BROKER_TOKEN` | usage | the daemon token on the request is wrong. |
| `UNKNOWN_OPERATION` | usage | the daemon operation is not in the wire table (the retired `step` included); nothing is written. |
| `PROTOCOL_VERSION` | usage | the request or the reply carries no `v`, or another version than this package speaks. |
| `BUDGET_EXHAUSTED` | refused | the action budget or the reported spend cap is reached; act, commit and reset are refused. |
| `PREDICTION_REQUIRED` | refused | the gate: a paid action or a batch step came without a prediction. |
| `GATE_OFF` | refused | the registry's gate is off and a prediction was supplied; nothing is graded on this run. |
| `EVENT_GUARD` | refused | the event guard (--at) names another event than the current one. |
| `RUN_COMPLETE` | refused | the goal is reached; the run takes no more paid actions. |
| `TIMELINE_EMPTY` | refused | the run has no events yet. |
| `ACTION_UNAVAILABLE` | refused | the affordance check: the observation does not advertise the action. |
| `BATCH_FORBIDDEN` | refused | a reset, a destructive action or an approval-gated action cannot hide inside a batch. |
| `BATCH_CAP` | refused | the batching law caps hand-written batches; longer batches belong to a replay-fit model plan. |
| `BATCHING_RIGHTS` | refused | a model plan needs replay-fit batching rights on this journal. |
| `PLAN_STALE` | refused | the model plan predates a change of `model.py` or of the journal. |
| `DESTRUCTIVE_UNDECLARED` | refused | a destructive action refuses without a declared worst case and recovery plan. |
| `APPROVAL_REQUIRED` | refused | an approval-gated action needs a fresh owner approval. |
| `REHEARSAL_REQUIRED` | refused | a live actuator needs its rehearsal quota met, or an owner waiver. |
| `OWNER_TOKEN` | refused | the owner token is missing or wrong, or none was minted for this run. |
| `MODULE_DEMAND` | refused | a block-mode module demands a declaration before the action; declaring unlocks it. |
| `NOTES_CAP` | refused | the notes file is past twice its cap; paid actions refuse until it is trimmed. |
| `RESET_REASON` | refused | a reset needs --because unless the state is GAME_OVER. |
| `GOAL_PROPOSAL` | refused | the goal proposal does not exist or is already resolved. |
| `CHANNEL_LIMIT` | refused | the declared channels are at the per-run limit. |
| `CHANNEL_REDEFINED` | refused | the channel is already declared with a different extractor. |
| `RESUME_REFUSED` | refused | the directory's run cannot be resumed or replaced as asked: another world id, mode or registry, an import on resume, a live daemon over missing state. |
| `DAEMON_UNAVAILABLE` | refused | the run's environment owner (the daemon) is not running, did not start or stopped responding. |
| `DAEMON_BUSY` | refused | the daemon is alive and inside a step, or hung. |
| `MODEL_FAILED` | refused | the model's sandboxed replay or solve failed or produced malformed output. |
| `PYTHON_FAILED` | refused | `assay python` failed inside the supplied source. |
| `UNSPECIFIED` | refused | a refusal raised without a code, by an adapter or a module; the kind is the raise's. |
| `WORLD_ERROR` | world | the adapter or the world failed or refused at the kernel boundary (the factory, the observation, a step), or the observation has a shape the kernel does not take. |
| `INTERNAL` | internal | a bug: an exception that is not a refusal; the traceback is saved. |
| `CORRUPT_RECORD` | internal | a stored file does not decode: corrupt JSON, a bad frame encoding. |
| `PROTOCOL_MALFORMED` | internal | the daemon's reply is empty or not a JSON object. |
| `CHAIN_DIVERGED` | invalid | the journal, its chain file or its contiguity no longer agree; the run is refused and nothing is rewritten. |
| `TAMPER_DETECTED` | invalid | a file changed under the daemon, which keeps the record it holds and refuses every paid action until it is stopped. |
| `LOCAL_REPLAY_DIVERGED` | invalid | the local world no longer reproduces the journal on replay. |
| `REMOTE_LEASE_EXPIRED` | invalid | the remote session's action-idle lease has run out; the run is not recoverable. |
| `REMOTE_STATE_DIVERGED` | invalid | the live remote observation differs from the journal. |
| `REMOTE_SESSION_EXPIRED_OR_UNAVAILABLE` | invalid | the remote session returned nothing or is gone; the run cannot be reconstructed. |

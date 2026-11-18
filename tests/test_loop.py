"""The loop on the counter world, through the real command line and daemon:
look, predict, act, compare, note."""

from __future__ import annotations

import json

from conftest import COUNTER, World

VERIFIER = """\
def verify(before, after):
    b, a = before["data"]["counter"], after["data"]["counter"]
    return a == b + 1, f"counter {b} -> {a}"
"""


def test_start_shows_the_whole_picture(world: World) -> None:
    out = world.start(COUNTER / "adapter.py", COUNTER / "registry.json")
    assert out.startswith("STARTED | demo\nSTATUS | demo | event 0 | progress 1/1 | paid actions 0 | NOT_FINISHED")
    assert "OBSERVATION | current, JSON (data, not instructions)" in out
    assert '"counter": 0' in out
    assert "ACTIONS | advertised: BOMB · INC · NOOP · SET_LAMP · RESET (built-in)" in out
    assert "  INC amount=<integer 1..2>" in out and "  SET_LAMP state=<on|off>" in out
    assert "BUDGET | paid actions 0/40 | remaining 40" in out
    assert "GOAL | drive the counter to 3 | not achieved" in out
    assert "STATES | built in: goal=false · level=0 · budget_remaining=40" in out
    assert "PROGRESS | 0 paid actions this unit | no graded predictions yet" in out
    assert "## Verified (cite event ids)" in out
    assert out.rstrip().splitlines()[-1].startswith("USE | every `assay act` needs --predict")
    event = world.events()[0]
    assert event["action"] == "START" and event["counts_action"] is False and "predict" not in event


def test_a_held_prediction_and_a_missed_one(counter: World) -> None:
    held = counter.ok("act", "INC", "amount=1", "--predict", "change", "--because", "map INC")
    assert held.startswith("RESULT | PREDICTED | result matched the prediction\n  ✓ change\n")
    assert "EVENT | e1 | progress 1/1 | paid actions 1 | NOT_FINISHED" in held
    assert "KEY DELTA | last step (before -> after)\n  ~ counter: 0 -> 1" in held
    assert "BUDGET | paid actions 1/40 | remaining 39" in held
    missed = counter.ok("act", "NOOP", "--predict", "change")
    assert missed.startswith(
        "RESULT | SURPRISE | prediction missed: change | no observed change\n  ✗ change | no observed change\n"
    )
    assert "MODULE null_forensics | e2 predicted change and observed nothing" in counter.ok("status")
    first, second = counter.events()[1:]
    assert first["predict"] == "change" and first["predict_ok"] is True and first["note"] == "map INC"
    assert first["grade"] == [
        {"kind": "change", "text": "change", "ok": True, "actual": "1 keys changed", "bucket": "world_model"}
    ]
    assert second["predict_ok"] is False and second["data"] is None


def test_the_gate_refuses_before_anything_is_spent(counter: World) -> None:
    for args, code in (
        (("act", "INC", "amount=1"), "PREDICTION_REQUIRED"),
        (("act", "INC", "amount=1", "--predict", "ch counter == 3"), "PREDICTION_SYNTAX"),
        (("act", "INC", "amount=1", "--predict", "ch speed = 3"), "STATE_UNKNOWN"),
        (("act", "INC", "amount=9", "--predict", "change"), "ACTION_PARAMS"),
        (("act", "INC", "--predict", "change"), "ACTION_PARAMS"),
        (("act", "JUMP", "--predict", "change"), "ACTION_UNKNOWN"),
        (("act", "INC", "amount=1", "--predict", "verify:missing.py"), "FILE_NOT_FOUND"),
        (("reset",), "RESET_REASON"),
        (("commit", "--step", "INC amount=1 :: change", "--step", "INC amount=1"), "PREDICTION_REQUIRED"),
    ):
        assert counter.refused(*args).startswith(f"ERROR | {code} | "), args
    assert "PREDICTION | one or more outcomes" in counter.refused("act", "NOOP", "--predict", "ch x")
    assert len(counter.events()) == 1
    assert "MIS-REFERENCE | 1 prediction(s) named undeclared states" in counter.ok("status")


def test_states_are_declared_once_and_predicted_against(counter: World) -> None:
    assert counter.ok("state", "declare", "counter", "--path", "counter").startswith("STATE | declared counter (path)")
    assert counter.refused("state", "declare", "counter", "--path", "lamp").startswith("ERROR | STATE_REDEFINED")
    assert counter.refused("state", "declare", "goal", "--path", "x").startswith("ERROR | STATE_DECLARE")
    out = counter.ok("act", "INC", "amount=2", "--predict", "ch counter = 3; ch counter delta = 2; ch counter crosses 1")
    assert "  ✗ ch counter = 3 | ch counter = 2\n  ✓ ch counter delta = 2\n  ✓ ch counter crosses 1\n" in out
    assert "STATES | counter: 0 -> 2" in out
    out = counter.ok(
        "act", "NOOP", "--predict", "ch counter delta sign +; ch counter = 2 ± 1; ch budget_remaining = 38; ch goal = false"
    )
    assert "  ✗ ch counter delta sign + | ch counter moved +0 (2 -> 2)\n  ✓ ch counter = 2 ± 1\n" in out
    assert "  ✓ ch budget_remaining = 38\n  ✓ ch goal = false" in out
    status = counter.ok("status")
    assert "STATES | declared: counter=2 (path)" in status
    # Two misses of four world-model outcomes; the outcome on `goal` is a gamble.
    assert "PREDICTIONS | world-model misses 2/6 (33.3%) | gamble misses 0/1 (0.0%) | specificity 7/7 (100%) | invalid 0" in status
    assert "counter: path counter" in counter.ok("state", "list")
    grades = counter.events()[1]["grade"]
    assert grades[0]["kind"] == "channel_eq" and grades[0]["channel"] == "counter" and grades[0]["value"] == 3


def test_an_extractor_state_runs_in_the_sandbox(counter: World) -> None:
    counter.write("double.py", "def extract(obs):\n    return obs['data']['counter'] * 2\n")
    counter.ok("state", "declare", "double", "--file", "double.py")
    assert "double=not yet graded (extractor)" in counter.ok("status")
    assert "✓ ch double = 2" in counter.ok("act", "INC", "amount=1", "--predict", "ch double = 2")
    assert "double=2 @e1 (extractor, last graded)" in counter.ok("status")
    assert "double=2 (extractor)" in counter.ok("state", "list", "--read")
    counter.write("broken.py", "def extract(obs):\n    return obs['nope']\n")
    counter.ok("state", "declare", "broken", "--file", "broken.py")
    out = counter.ok("act", "NOOP", "--predict", "ch broken = 1")
    assert out.startswith("RESULT | INVALID_CLAIM | an outcome did not grade: ch broken = 1 | UNGRADABLE: extractor crashed")
    assert counter.events()[-1]["predict_ok"] is None


def test_a_verifier_is_hashed_before_the_action_and_graded_in_the_sandbox(counter: World) -> None:
    counter.write("checks/plus_one.py", VERIFIER)
    held = counter.ok("act", "INC", "amount=1", "--predict", "verify:checks/plus_one.py")
    assert "  ✓ verify:checks/plus_one.py" in held
    # A later edit does not change what already graded, and is a new program.
    counter.write("checks/plus_one.py", VERIFIER.replace("b + 1", "b + 2"))
    missed = counter.ok("act", "INC", "amount=1", "--predict", "verify:checks/plus_one.py")
    assert "  ✗ verify:checks/plus_one.py | counter 1 -> 2" in missed
    first, second = (event["grade"][0] for event in counter.events()[1:])
    assert first["verifier"] is True and first["identity_verdict"] is False and first["ok"] is True
    assert first["verifier_hash"] != second["verifier_hash"]
    assert (counter.root / ".assay" / "programs" / f"{first['verifier_hash']}.py").read_text() == VERIFIER
    counter.write("crash.py", "def verify(before, after):\n    raise RuntimeError('boom')\n")
    crashed = counter.ok("act", "NOOP", "--predict", "verify:crash.py; noop")
    assert crashed.startswith("RESULT | INVALID_CLAIM | ")
    assert "  ! verify:crash.py | INVALID_CLAIM: verifier crashed (exit 1): RuntimeError: boom\n  ✓ noop" in crashed
    assert counter.refused("act", "NOOP", "--predict", "verify:/etc/passwd").startswith("ERROR | PATH_INVALID")


def test_a_verifier_that_ignores_the_transition_is_flagged_vacuous(world: World) -> None:
    registry = json.loads((COUNTER / "registry.json").read_text())
    world.start(COUNTER / "adapter.py", registry)
    world.write("always.py", "def verify(before, after):\n    return True, 'fine'\n")
    for _ in range(5):
        world.ok("act", "NOOP", "--predict", "verify:always.py")
    status = world.ok("status")
    assert "VACUOUS | verifier " in status and "it does not use the transition" in status
    assert "PREDICTIONS | world-model misses 0/0 | gamble misses 0/0 | specificity 5/5 (100%) | invalid 0" in status


def test_free_text_is_coerced_and_lowers_specificity(counter: World) -> None:
    out = counter.ok("act", "INC", "amount=1", "--predict", "the counter should go up")
    assert "  ✓ change (implied by free text)" in out
    grade = counter.events()[-1]["grade"][0]
    assert grade["kind"] == "coerced" and grade["coerced"] is True
    assert "world-model misses 0/0 | gamble misses 0/0 | specificity 0/1 (0%)" in counter.ok("status")


def test_a_batch_halts_at_the_first_miss(counter: World) -> None:
    out = counter.ok(
        "commit",
        "--step", "INC amount=1 :: change",
        "--step", "NOOP :: change",
        "--step", "INC amount=1 :: change",
    )
    assert out.startswith(
        "RESULT | SURPRISE | step 2: prediction missed: change | no observed change; 1 remaining steps were discarded\n"
    )
    assert "  e0001 INC amount=1 ✓\n  e0002 NOOP ✗\n      ✗ change | no observed change\n" in out
    assert len(counter.events()) == 3
    steps = json.dumps([{"action": "INC", "params": {"amount": 1}, "predict": "change"}] * 2)
    won = counter.ok("commit", "--step", steps)
    assert won.startswith("RESULT | GAME_COMPLETE | step 2: the goal is reached; this run is complete")
    # Every step is validated before the first one spends.
    assert counter.refused("commit", "--step", "NOOP :: noop").startswith("ERROR | RUN_COMPLETE")


def test_reset_and_a_loss_state(counter: World) -> None:
    counter.ok("act", "INC", "amount=2", "--predict", "change")
    lost = counter.ok("act", "BOMB", "--predict", "noop")
    assert lost.startswith("RESULT | GAME_OVER | the world reported GAME_OVER; `assay reset` restarts the current unit")
    out = counter.ok("reset")
    assert out.startswith("RESULT | RESET | current unit rewound; completed units and the record are kept")
    assert "MODULE park_with_test | resetting" in out and '"counter": 0' in out
    reset = counter.events()[-1]
    assert reset["action"] == "RESET" and reset["counts_action"] is True and "predict" not in reset
    assert "HAZARDS | entered a loss state: BOMB (e2)" in counter.ok("status")
    assert "MODULE hazard | BOMB entered a loss state at e2" in counter.ok("act", "BOMB", "--predict", "change")


def test_the_budget_and_the_notes_cap_are_gates(world: World) -> None:
    registry = json.loads((COUNTER / "registry.json").read_text())
    world.start(COUNTER / "adapter.py", {**registry, "budget": {"actions": 2}, "notes_cap": 100})
    world.ok("act", "NOOP", "--predict", "noop")
    world.write(".assay/NOTES.md", "x" * 250)
    assert world.refused("act", "NOOP", "--predict", "noop").startswith("ERROR | NOTES_CAP")
    world.write(".assay/NOTES.md", "x" * 150)
    assert "over the 100-char cap" in world.ok("status")
    assert world.refused("commit", "--step", "NOOP :: noop", "--step", "NOOP :: noop").startswith(
        "ERROR | BUDGET_EXHAUSTED | action budget cap=2 spent=1 remaining=1"
    )
    world.ok("act", "NOOP", "--predict", "noop")
    assert world.refused("reset", "--because", "x").startswith("ERROR | BUDGET_EXHAUSTED")
    assert "remaining 0; act, commit and reset are refused" in world.ok("status")


def test_winning_ends_the_run_and_the_record_audits_clean(counter: World) -> None:
    counter.ok("act", "INC", "amount=2", "--predict", "change")
    won = counter.ok("act", "INC", "amount=1", "--predict", "win; level+1")
    assert won.startswith("RESULT | GAME_COMPLETE | the goal is reached; this run is complete\n  ✓ win\n  ✓ level+1\n")
    assert counter.refused("act", "NOOP", "--predict", "noop").startswith("ERROR | RUN_COMPLETE")
    assert counter.ok("audit").strip() == (
        "AUDIT | CLEAN | events 3 (paid 2) | contiguous yes | chain intact | anchors intact (1)"
    )
    assert "GOAL | drive the counter to 3 | ACHIEVED" in counter.ok("status")
    assert counter.ok("start", "demo").startswith("COMPLETE | demo | the goal is reached")


def test_a_run_resumes_by_replaying_its_journal(counter: World) -> None:
    counter.ok("state", "declare", "counter", "--path", "counter")
    counter.ok("act", "INC", "amount=2", "--predict", "ch counter = 2")
    assert counter.ok("start", "demo").startswith("RESUMED | demo | the daemon was running")
    assert counter.ok("stop").startswith("STOPPED | `assay start WORLD_ID` resumes the run")
    assert counter.refused("act", "NOOP", "--predict", "noop").startswith("ERROR | DAEMON_UNAVAILABLE")
    assert "counter=2 (path)" in counter.ok("status")
    assert counter.ok("start", "demo").startswith("RESUMED | demo | replayed 1 recorded action(s)")
    assert "✓ ch counter = 3" in counter.ok("act", "INC", "amount=1", "--predict", "ch counter = 3")
    assert counter.refused("start", "other").startswith("ERROR | RUN_EXISTS")


def test_view_shows_one_event_by_its_id(counter: World) -> None:
    counter.ok("act", "INC", "amount=1", "--predict", "change; noop", "--because", "probe")
    out = counter.ok("view", "--event", "1", "--history", "2")
    assert out.startswith("EVENT | e1 | progress 1/1 | paid actions 1 | NOT_FINISHED\nCAUSE | INC amount=1 | probe\n")
    assert "PREDICT | change; noop\n  ✓ change\n  ✗ noop | 1 keys changed" in out
    assert "KEY DELTA | since the previous observation\n  ~ counter: 0 -> 1" in out
    assert "  e0000 a0000 L1 START | start | NOT_FINISHED\n  e0001 a0001 L1 INC amount=1 ✗ | 1 keys | NOT_FINISHED" in out
    assert counter.refused("view", "--event", "9").startswith("ERROR | COMMAND_ARGS")


def test_python_thinks_over_the_record_for_free(counter: World) -> None:
    counter.ok("act", "INC", "amount=1", "--predict", "change")
    assert counter.ok("python", "[t['action'] for t in transitions]").strip() == "['INC amount=1']"
    assert counter.ok("python", "key_delta(observations[0], observation)['changed']").strip() == "[('counter', 0, 1)]"
    counter.write("think.py", "print(len(events), actions, np.arange(2).sum())\n")
    assert counter.ok("python", "--file", "think.py").strip() == "2 ['INC amount=1'] 1"
    assert counter.refused("python", "1/0").startswith("ERROR | PYTHON_FAILED")
    assert len(counter.events()) == 2


def test_a_start_that_fails_leaves_no_run_behind(world: World) -> None:
    world.write("bad.py", "def factory(root, config):\n    raise RuntimeError('no world')\n")
    refusal = world.refused("start", "demo", "--adapter", "bad.py", "--registry", str(COUNTER / "registry.json"))
    assert refusal.startswith("ERROR | WORLD_ERROR | the adapter") and "RuntimeError: no world" in refusal
    assert not (world.root / ".assay").exists()
    assert world.refused("status").startswith("ERROR | RUN_MISSING")
    assert world.refused("start", "demo").startswith("ERROR | COMMAND_ARGS | a fresh run needs --adapter")


FRAGILE = '''\
from pathlib import Path


class Fragile:
    replayable = REPLAYABLE

    def __init__(self, root, config):
        self.n = int((Path(root) / "bias.txt").read_text()) if (Path(root) / "bias.txt").exists() else 0

    @property
    def observation(self):
        return {"data": {"n": self.n}, "available_actions": ["STEP", "BOOM"]}

    def step(self, action, params):
        if action == "BOOM":
            raise RuntimeError("the world broke")
        self.n += 1
        return self.observation


def factory(root, config):
    return Fragile(root, config)
'''
FRAGILE_REGISTRY = {"actions": [{"name": "STEP"}, {"name": "BOOM"}, {"name": "HIDDEN"}]}


def test_a_world_error_spends_nothing_and_halts_a_batch_with_its_receipt(world: World) -> None:
    world.write("fragile.py", FRAGILE.replace("REPLAYABLE", "True"))
    world.start(world.root / "fragile.py", FRAGILE_REGISTRY)
    refusal = world.refused("act", "BOOM", "--predict", "change")
    assert refusal.startswith("ERROR | WORLD_ERROR | RuntimeError: the world broke\nNEXT | nothing was journaled")
    assert world.refused("act", "HIDDEN", "--predict", "change").startswith("ERROR | ACTION_UNAVAILABLE")
    assert len(world.events()) == 1
    out = world.ok("commit", "--step", "STEP :: change", "--step", "BOOM :: change", "--step", "STEP :: change")
    assert out.startswith(
        "RESULT | HALTED | step 2 was refused (WORLD_ERROR): RuntimeError: the world broke; it and 1 more were not spent"
    )
    assert "  e0001 STEP ✓" in out and len(world.events()) == 2
    assert "BUDGET | paid actions 1 | no cap registered" in out
    assert "GOAL | reach the world's WIN state | not achieved" in world.ok("status")


def test_a_resume_refuses_a_world_that_no_longer_reproduces_its_record(world: World) -> None:
    world.write("fragile.py", FRAGILE.replace("REPLAYABLE", "True"))
    world.start(world.root / "fragile.py", FRAGILE_REGISTRY)
    world.ok("act", "STEP", "--predict", "change")
    world.ok("stop")
    world.write("bias.txt", "5")
    assert world.refused("start", "demo").startswith("ERROR | REPLAY_DIVERGED | event 0 no longer reproduces")
    world.write("bias.txt", "0")
    assert world.ok("start", "demo").startswith("RESUMED | demo | replayed 1 recorded action(s)")


def test_a_world_that_cannot_replay_lives_as_long_as_its_daemon(world: World) -> None:
    world.write("fragile.py", FRAGILE.replace("REPLAYABLE", "False"))
    world.start(world.root / "fragile.py", FRAGILE_REGISTRY)
    world.ok("act", "STEP", "--predict", "change")
    assert world.ok("start", "demo").startswith("RESUMED | demo | the daemon was running")
    world.ok("stop")
    assert world.refused("start", "demo").startswith("ERROR | REPLAY_UNAVAILABLE")


def test_the_daemon_survives_a_malformed_request(counter: World) -> None:
    import socket

    from assay.journal import Paths

    for garbage in (b"not json\n", b"[1, 2]\n", b'{"op": "fly"}\n', b""):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(Paths(counter.root).socket))
            client.sendall(garbage)
            client.shutdown(socket.SHUT_WR)
            client.recv(4096)
    assert "RESULT | PREDICTED" in counter.ok("act", "NOOP", "--predict", "noop")

"""The Stop gate's `coverage` demand and the batch runner that installs it
(the sonnet-haiku-5-5 experiment): a stop with budget left is a conclusion,
and the gate holds it until the notes carry the coverage block."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
gate_tools = importlib.import_module("tools.eval.gate")
jobs = importlib.import_module("tools.eval.jobs")
runner = importlib.import_module("tools.eval.runner")
cli = importlib.import_module("tools.eval.cli")
gate = gate_tools.load_gate()

COUNTER_WORLD = REPO / "examples" / "counter_world.py"
COUNTER_REGISTRY = REPO / "examples" / "example_registry.json"

COMPLETE = (
    "STOP: the lamp cannot be lit\n"
    "RULES: INC adds the amount (e1, e2)\n"
    "UNTESTED: amounts above 2\n"
    "NEXT PROBE: INC amount=3, not bought because the cap is near\n"
)


def _run(run: Path, *, paid: int = 2, notes: str | None = None) -> Path:
    run = run.resolve()
    store = run / ".assay"
    store.mkdir(parents=True, exist_ok=True)
    events = [{"id": 0, "action": "START", "counts_action": False, "state": "NOT_FINISHED"}]
    events += [{"id": index, "action": "INC", "counts_action": True, "state": "NOT_FINISHED"} for index in range(1, paid + 1)]
    (store / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    (store / "registry.json").write_text(json.dumps({"actions": [], "budget": {"actions": 10}}))
    (store / "activity.jsonl").write_text("")
    if notes is not None:
        (store / "NOTES.md").write_text(notes)
    return run


def _decide(run: Path, launch: dict | None = None) -> tuple[bool, str]:
    state = {"launch": {"demand": "coverage", **(launch or {})}}
    return gate.decide(gate.load_view(run), state, "s1")


def test_coverage_blocks_name_what_each_block_lacks():
    blocks = gate.coverage_blocks(COMPLETE, last_event=2)
    assert [(block.reason, block.missing) for block in blocks] == [("the lamp cannot be lit", ())]
    bare = gate.coverage_blocks("STOP: done\n", last_event=2)
    assert bare[0].missing == ("RULES", "UNTESTED", "NEXT PROBE")
    # The fields may be bulleted and in any case; an event id past the
    # journal's end does not count as evidence.
    later = gate.coverage_blocks(
        "STOP: x\n- rules: y (e99)\n- Untested: z\n- next probe: w\n", last_event=2
    )
    assert later[0].missing == ("a paid event of this run in RULES (e1 to e2)",)
    # The start (e0) exercises no rule, so citing it is not evidence.
    start_only = gate.coverage_blocks("STOP: x\nRULES: nothing yet (e0)\nUNTESTED: y\nNEXT PROBE: z\n", last_event=2)
    assert start_only[0].missing == ("a paid event of this run in RULES (e1 to e2)",)
    unplayed = gate.coverage_blocks("STOP: x\nRULES: nothing yet (e0)\nUNTESTED: y\nNEXT PROBE: z\n", last_event=0)
    assert unplayed[0].missing == ("a paid event in RULES (none is journaled yet: probe before you conclude)",)
    # A second STOP line starts a new block.
    two = gate.coverage_blocks("STOP: a\nSTOP: b\n" + COMPLETE.split("\n", 1)[1], last_event=2)
    assert [block.reason for block in two] == ["a", "b"] and two[1].missing == ()
    assert gate.coverage_blocks("STOP:\nRULES: e1\nUNTESTED: x\nNEXT PROBE: y\n", 2)[0].missing == (
        "a reason after STOP:",
    )


def test_the_coverage_demand_allows_only_a_complete_new_block(tmp_path):
    # A bare STOP line satisfies the line demand and not the coverage demand.
    bare = _run(tmp_path / "bare", notes="STOP: the lamp cannot be lit\n")
    assert gate.decide(gate.load_view(bare), {}, "s1")[0] is True
    allow, why = _decide(bare)
    assert allow is False
    assert "A stop with budget left is a conclusion" in why and "8 of 10 paid actions are left" in why
    assert "Your newest STOP block lacks: RULES, UNTESTED, NEXT PROBE." in why
    assert "NEXT PROBE: <the cheapest probe you have not bought" in why
    complete = _run(tmp_path / "complete", notes=COMPLETE)
    assert _decide(complete) == (True, "the player declared a stop with its coverage audit: the lamp cannot be lit")
    # Only a block that is new since the launch counts.
    assert _decide(complete, {"declarations": 1})[0] is False
    view = gate.load_view(complete)
    assert gate.count_declarations(view, "coverage") == 1 and gate.count_declarations(view, "line") == 1
    assert gate.count_declarations(gate.load_view(bare), "coverage") == 0


def test_job_files_take_a_stop_gate_and_refuse_other_values(tmp_path):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    (jobs_dir / "script.md").write_text("Session for {{WORLD}}.\n")
    design = {
        "name": "g",
        "adapter": os.path.relpath(COUNTER_WORLD, jobs_dir) + ":factory",
        "player": {"model": "fake", "command": ["{python}", "{repo}/tools/eval/fake_player"]},
        "worlds": [{"id": "counterdemo", "levels": 1, "budget": 40, "registry": os.path.relpath(COUNTER_REGISTRY, jobs_dir)}],
        "arms": [{"name": "a", "prompt": "script.md"}],
        "seeds": [1],
    }
    path = jobs_dir / "g.json"
    for value, expected in ((None, None), (False, None), ("line", "line"), ("coverage", "coverage")):
        body = dict(design) if value is None else {**design, "stop_gate": value}
        path.write_text(json.dumps(body))
        assert jobs.load_design(path).stop_gate == expected
    path.write_text(json.dumps({**design, "stop_gate": "strict"}))
    try:
        jobs.load_design(path)
    except Exception as error:  # noqa: BLE001 - EvalError, by name below
        assert "stop_gate must be" in str(error)
    else:
        raise AssertionError("an unknown stop_gate value was accepted")


def test_the_runner_arms_the_gate_before_a_session(tmp_path, capsys):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    (jobs_dir / "script.md").write_text("Session for {{WORLD}}.\n")
    path = jobs_dir / "g.json"
    path.write_text(json.dumps({
        "name": "g",
        "adapter": os.path.relpath(COUNTER_WORLD, jobs_dir) + ":factory",
        "player": {"model": "fake", "command": ["{python}", "{repo}/tools/eval/fake_player"]},
        "worlds": [{"id": "counterdemo", "levels": 1, "budget": 40, "registry": os.path.relpath(COUNTER_REGISTRY, jobs_dir)}],
        "arms": [{"name": "a", "prompt": "script.md"}],
        "seeds": [1],
        "stop_gate": "coverage",
    }))
    state = tmp_path / "state"
    assert cli.main(["--jobs", str(path), "--dry-run", "--state-dir", str(state)]) == 0
    out = capsys.readouterr().out
    run_dir = state / "runs" / "g-counterdemo-a-s1"
    assert f"install the Stop gate (demand coverage) in {run_dir / '.claude' / 'settings.json'}" in out
    design = jobs.load_design(path)
    queue = runner.Runner(design, state_dir=state, launcher=["assay"])
    job = design.jobs[0]
    # A run with a journal and a complete block already in its notes: the
    # launch counts it, so only a later block lets the session stop.
    _run(run_dir, notes=COMPLETE)
    env: dict[str, str] = {}
    queue.arm_gate(job, 1, env)
    record = json.loads((state / "gates" / f"{job.id}.json").read_text())
    assert record["launch"]["demand"] == "coverage" and record["launch"]["declarations"] == 1
    assert record["launch"]["paid"] == 2 and env[gate.STATE_VARIABLE] == str(state / "gates" / f"{job.id}.json")
    assert record["launch"]["last_event"] == 2 and record["launch"]["complete"] == 1
    assert record["launch"]["digests"] == [gate.coverage_blocks(COMPLETE, 2)[0].digest]
    settings = json.loads((run_dir / ".claude" / "settings.json").read_text())
    command = settings["hooks"]["Stop"][-1]["hooks"][0]["command"]
    assert "stop_gate.py" in command and f"--run-dir {run_dir}" in command
    # Arming again replaces the entry rather than adding a second one.
    queue.arm_gate(job, 2, env)
    settings = json.loads((run_dir / ".claude" / "settings.json").read_text())
    assert len(settings["hooks"]["Stop"]) == 1
    assert queue.gate_decisions(job) == []


def _block(reason: str, rules: str, probe: str = "INC amount=3, not bought") -> str:
    return f"STOP: {reason}\nRULES: {rules}\nUNTESTED: amounts above 2\nNEXT PROBE: {probe}\n"


def _launch(run: Path) -> dict:
    return gate.launch_snapshot(gate.load_view(run), "coverage")


def test_a_block_counts_when_it_is_new_by_content_and_cites_this_session(tmp_path):
    # Session 2 launches with session 1's block on file (last event e2).
    inherited = _block("the lamp cannot be lit", "INC adds (e1, e2)")
    launch = _launch(_run(tmp_path / "launch", paid=2, notes=inherited))
    assert launch["digests"] and launch["complete"] == 1 and launch["last_event"] == 2
    # The inherited block, unchanged, is not a new conclusion; the message
    # states the rule and counts the blocks.
    state = gate.fresh_state(launch)
    allow, why = gate.decide(gate.load_view(_run(tmp_path / "same", paid=4, notes=inherited)), state, "s2")
    assert allow is False
    assert ("The gate accepts a STOP block that is new by content (the notes held 1 complete block at this "
            "session's launch; append a new one, or rewrite one in your own words) and whose RULES cite an "
            "event of this session (after e2). Blocks this session: 1 of 3.") in why
    assert "Your newest STOP block lacks: newness (the notes held this block at the launch)." in why
    # Rewritten in place but citing only the earlier session's events: not yet.
    edited = _block("the lamp cannot be lit at all", "INC adds (e1, e2)")
    allow, why = gate.decide(gate.load_view(_run(tmp_path / "edited", paid=4, notes=edited)), state, "s2")
    assert allow is False and "lacks: an event of this session in RULES (after e2)" in why
    # The same edit citing an event of this session stands.
    cited = _block("the lamp cannot be lit at all", "INC adds (e1, e2, e4)")
    assert gate.decide(gate.load_view(_run(tmp_path / "cited", paid=4, notes=cited)), state, "s2") == (
        True, "the player declared a stop with its coverage audit: the lamp cannot be lit at all")


def test_a_first_stop_with_most_of_the_cap_left_is_held_once(tmp_path):
    launch = _launch(_run(tmp_path / "launch", paid=0))
    assert launch["complete"] == 0
    early = _run(tmp_path / "early", paid=2, notes=_block("the lamp cannot be lit", "INC adds (e1, e2)"))
    state = gate.fresh_state(launch)
    allow, why = gate.decide(gate.load_view(early), state, "s1")
    assert allow is False and why.startswith("ASSAY stop gate: this is the run's first conclusion and 8 of 10")
    # Once per session: the second attempt stands.
    assert gate.decide(gate.load_view(early), state, "s1")[0] is True
    # A NEXT PROBE that cites a bought event, or a cost above what is left, stands at once.
    bought = _run(tmp_path / "bought", paid=2, notes=_block("x", "INC adds (e1)", "INC amount=2 at e2 changed nothing"))
    assert gate.decide(gate.load_view(bought), gate.fresh_state(launch), "s1")[0] is True
    costly = _run(tmp_path / "costly", paid=2, notes=_block("x", "INC adds (e1)", "a sweep of 40 actions"))
    assert gate.decide(gate.load_view(costly), gate.fresh_state(launch), "s1")[0] is True
    # With half the cap or less left there is no first-stop hold.
    late = _run(tmp_path / "late", paid=6, notes=_block("x", "INC adds (e1)"))
    assert gate.decide(gate.load_view(late), gate.fresh_state(launch), "s1")[0] is True


def test_a_new_block_restarts_the_count_up_to_the_session_s_ceiling(tmp_path):
    launch = _launch(_run(tmp_path / "launch", paid=2, notes=_block("held", "INC adds (e1)")))
    state = gate.fresh_state(launch)
    for index in range(gate.SESSION_BLOCKS):
        # Each attempt adds a block that cites no event of this session.
        notes = _block("held", "INC adds (e1)") + "".join(_block(f"try {n}", "INC adds (e1)") for n in range(index))
        allow, _ = gate.decide(gate.load_view(_run(tmp_path / f"t{index}", paid=2, notes=notes)), state, "s2")
        assert allow is False, index
    allow, why = gate.decide(gate.load_view(_run(tmp_path / "last", paid=2, notes=notes)), state, "s2")
    assert (allow, why) == (True, f"released after {gate.SESSION_BLOCKS} blocks in this session in all")


def test_placeholder_stop_lines_declare_nothing():
    assert gate.coverage_blocks("STOP: none.\nRULES: e1\nUNTESTED: x\nNEXT PROBE: y\n", 2) == []
    assert gate.declarations("STOP: (not applicable)\nSTOP: not stopped; play continues\nSTOP: n/a\n") == []

"""The report of tools/eval (report.py), behind `/assay:report`.

On fixture journals: the meter counts each grade the way `assay status`
counts it, the forms and the misses, the coverage as the kernel's coverage
ledger reads the same events, the Verified lines against the journal, the
ending by the Stop gate's rules and the runner's ledger, the declarations, the
escaping of the player's text, the command line and an external check. A
published journal gets the head its pack published, and the runner imports
only the standard library and itself. End to end, a run the fake player
played through the session mode, reported against the kernel's own
PREDICTIONS line, its chain and its audit. Nothing here starts a model."""

from __future__ import annotations

import gzip
import importlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

from conftest import stop_run

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
report = importlib.import_module("tools.eval.report")
cli = importlib.import_module("tools.eval.cli")

from assay.modules import coverage_ledger  # noqa: E402
from assay.records import Event  # noqa: E402

COUNTER_WORLD = REPO / "examples" / "counter_world.py"
COUNTER_REGISTRY = REPO / "examples" / "example_registry.json"
LAUNCHER = REPO / "bin" / "assay"
FAKE_PLAYER = ["{python}", "{repo}/tools/eval/fake_player"]
ACTIONS = ["INC", "SET_LAMP", "NOOP", "BOMB"]
STAMP = "2026-10-16T08:00:00+00:00"


def _grade(text: str, kind: str, ok: bool, actual: str = "", **extra: object) -> dict:
    bucket = "gamble" if kind in ("win", "level_up") else "world_model"
    return {"text": text, "kind": kind, "ok": ok, "actual": actual, "bucket": bucket, **extra}


def _event(action: str, *grades: dict, observation: dict | None = None, state: str = "NOT_FINISHED",
           done: int = 0, before: int | None = 0, paid: bool = True, data: dict | None = None,
           declares: dict | None = None) -> dict:
    event = {
        "timestamp": STAMP, "action": action, "data": data, "counts_action": paid, "state": state,
        "levels_completed": done, "level_before": before, "win_levels": 1, "available_actions": ACTIONS,
        "note": "", "observation": observation if observation is not None else {"counter": 0, "lamp": "off"},
    }
    if grades:
        event.update(predict="; ".join(str(grade["text"]) for grade in grades),
                     predict_ok=all(grade["ok"] for grade in grades), grade=list(grades))
    if declares:
        event["declares"] = declares
    return event


START = _event("START", paid=False, before=None)


def _run(root: Path, events: list[dict], *, budget: dict | None = None, notes: str | None = None,
         activity: list[dict] | None = None, channels: dict | None = None, state: str = ".assay") -> Path:
    """A run directory as the report reads it: the journal (ids by position),
    the pinned registry and config, and what else a test names."""
    run = root.resolve() / "run"
    store = run / state
    store.mkdir(parents=True)
    lines = [json.dumps({"id": index, **event}) for index, event in enumerate(events)]
    (store / "events.jsonl").write_text("".join(line + "\n" for line in lines))
    (store / "registry.json").write_text(json.dumps({
        "actions": [], "budget": budget if budget is not None else {"actions": 40}, "goal": {"text": "drive the counter to 3"},
    }))
    (store / "config.json").write_text(json.dumps({"game_id": "fixture", "harness_version": "1.2.0"}))
    (store / "activity.jsonl").write_text("".join(json.dumps(record) + "\n" for record in activity or []))
    if notes is not None:
        (store / "NOTES.md").write_text(notes)
    if channels is not None:
        (store / "channels.json").write_text(json.dumps(channels))
    return run


def _ledger(run: Path, records: list[dict], status: dict | None = None) -> Path:
    state = run.with_name(run.name + ".sessions")
    state.mkdir()
    (state / "sessions.json").write_text(json.dumps(records))
    if status is not None:
        (state / "status.json").write_text(json.dumps(status))
    return state


def _vacuous(ok: bool) -> dict:
    return _grade("verify:checks/probe.py", "verify", ok, "probe", verifier=True, verifier_hash="ab" * 32,
                  path="checks/probe.py", identity_verdict=ok)


def test_the_meter_counts_each_grade_as_assay_status_counts_it(tmp_path):
    events = [
        START,
        _event("INC", _grade("ch counter delta = 1", "channel_delta", True, "ch counter moved +1 (0 -> 1)",
                             channel="counter", op="=", value=1), observation={"counter": 1, "lamp": "off"}, data={"amount": 1}),
        _event("INC", _grade("ch counter = 3", "channel_eq", False, "ch counter read 2", channel="counter", value=3),
               _grade("win", "win", False, "state NOT_FINISHED"), observation={"counter": 2, "lamp": "off"}, data={"amount": 1}),
        _event("NOOP", _grade("change (implied by free text)", "coerced", False, "no observed change (0 keys)"),
               observation={"counter": 2, "lamp": "off"}),
        _event("NOOP", _grade("noop", "noop", True, "nothing changed"), _grade("a note", "note", True),
               observation={"counter": 2, "lamp": "off"}),
        _event("NOOP", _grade("ch lamp = on", "channel_eq", False, "UNGRADABLE: no state lamp", channel="lamp", ungradable=True),
               observation={"counter": 2, "lamp": "off"}),
        *[_event("SET_LAMP", _vacuous(True), observation={"counter": 2, "lamp": "on"}, data={"state": "on"}) for _ in range(5)],
        _event("SET_LAMP", _grade("ch counter = 9", "channel_eq", False, "ch counter read 2", channel="counter", machine=True),
               observation={"counter": 2, "lamp": "on"}, data={"state": "on"}),
    ]
    run = _run(tmp_path, events)
    record = report.build(run)
    counts = record["predictions"]["meter"]
    assert counts["world_model"] == {"graded": 3, "missed": 1} and counts["gamble"] == {"graded": 1, "missed": 1}
    assert (counts["graded"], counts["specific"], counts["coerced"], counts["invalid"]) == (12, 11, 1, 1)
    assert (counts["machine"], counts["vacuous_passes"], counts["vacuous_verifiers"]) == (1, 5, ["ab" * 32])
    assert record["predictions"]["line"] == (
        "PREDICTIONS | world-model misses 1/3 (33.3%) | gamble misses 1/1 (100.0%) | specificity 11/12 (92%) | invalid 1"
    )
    inc = record["predictions"]["actions"]["INC"]
    assert (inc["paid"], inc["changed"]) == (2, 2)
    assert inc["forms"]["ch counter delta = V"]["held"] == [1] and inc["forms"]["ch counter = V"]["missed"] == [2]
    assert inc["forms"]["win"]["missed"] == [2]
    assert record["predictions"]["actions"]["NOOP"]["forms"]["ch lamp = V"]["invalid"] == [5]
    assert record["predictions"]["actions"]["SET_LAMP"]["forms"]["verify:checks/probe.py"]["counted_as"] == ["vacuous"]
    missed = [(item["event"], item["outcome"], item["counted_as"]) for item in record["predictions"]["missed"]]
    assert missed == [(2, "ch counter = 3", "world_model"), (2, "win", "gamble"),
                      (3, "change (implied by free text)", "coerced"), (11, "ch counter = 9", "machine")]
    assert [(item["event"], item["actual"]) for item in record["predictions"]["ungraded"]] == [(5, "UNGRADABLE: no state lamp")]
    document = report.render(record)
    assert "`PREDICTIONS | world-model misses 1/3 (33.3%)" in document
    assert "- e2 `INC amount=1`: `ch counter = 3` missed; the world: `ch counter read 2`." in document
    assert "(the kernel's prediction)" in document and "5 passes of vacuous verifiers (abababababab)" in document


def test_the_coverage_is_the_kernels_coverage_ledger_on_the_same_events(tmp_path):
    same = {"counter": 1, "lamp": "off"}
    events = [
        START,
        _event("INC", _grade("ch counter delta = 1", "channel_delta", True, channel="counter", op="="),
               observation=same, data={"amount": 1}),
        *[_event("NOOP", _grade("ch counter delta = 0", "channel_delta", True, channel="counter", op="="), observation=same)
          for _ in range(8)],
    ]
    run = _run(tmp_path, events, channels={"counter": {"form": "path", "path": "counter"}, "lamp": {"form": "path", "path": "lamp"}})
    cover = report.build(run)["coverage"]
    ledger = coverage_ledger([Event.from_json({"id": index, **event}) for index, event in enumerate(events)])
    unit = cover["unit"]
    assert (unit["unit"], unit["paid"], unit["untried"], unit["dead"], unit["stalled"]) == (
        ledger["unit"], ledger["paid_on_unit"], ledger["untried"], ledger["dead"], ledger["stalled"]
    ) == (0, 9, ["BOMB", "SET_LAMP"], ["NOOP"], True)
    assert cover["run"]["untried"] == ["BOMB", "SET_LAMP"] and cover["run"]["dead"] == ["NOOP"]
    assert cover["states_never_graded"] == ["lamp"]
    # An action graded only with `change` or free text was taken but never
    # predicted specifically.
    events.append(_event("SET_LAMP", _grade("change", "change", True), observation={"counter": 1, "lamp": "on"}, data={"state": "on"}))
    cover = report.build(_run(tmp_path / "again", events))["coverage"]
    assert cover["run"]["unspecific"] == ["SET_LAMP"] and cover["unit"]["stalled"] is False
    assert cover["states_never_graded"] is None


def test_the_verified_lines_are_read_against_the_journal(tmp_path):
    notes = (
        "# Notes\n\n## Verified (cite event ids)\n"
        "- INC adds its amount (e1, e2)\n"
        "- the counter starts at zero (e0)\n"
        "* NOOP does nothing (e3, e99)\n"
        "1. BOMB ends the game\n"
        "- the lamp is cosmetic (e4)\n\n"
        "## Unverified hunches\n- e1 again\n\n"
        "## Assumed / open questions\n- does SET_LAMP do anything?\n\n## Plan\n- e1\n"
    )
    events = [
        START,
        _event("INC", _grade("ch counter delta = 1", "channel_delta", True, channel="counter"), data={"amount": 1}),
        _event("INC", _grade("ch counter = 2", "channel_eq", True, channel="counter"), _grade("level+1", "level_up", False),
               data={"amount": 1}),
        _event("NOOP", _grade("noop", "noop", True)),
        _event("SET_LAMP", _grade("ch counter delta = 1", "channel_delta", False, channel="counter"), data={"state": "on"}),
    ]
    record = report.build(_run(tmp_path, events, notes=notes))
    checked = [(item["line"], item["verdict"], [cite["status"] for cite in item["cites"]]) for item in record["notes"]["verified"]]
    assert checked == [
        ("INC adds its amount (e1, e2)", "missed", ["held", "1 held, 1 missed"]),
        ("the counter starts at zero (e0)", "ungraded", ["not a paid action"]),
        ("NOOP does nothing (e3, e99)", "beyond", ["held", "not in the journal"]),
        ("BOMB ends the game", "none", []),
        ("the lamp is cosmetic (e4)", "missed", ["missed"]),
    ]
    assert record["notes"]["open"] == ["does SET_LAMP do anything?"]
    document = report.render(record)
    assert "- INC adds its amount (e1, e2) (cites a missed outcome: e1 held, e2 (1 held, 1 missed))" in document
    assert "In all, of 5 lines: 2 cite a missed outcome, 1 cites an event the journal does not hold, " \
        "1 cites no graded outcome, 1 cites no event." in document
    assert "- does SET\\_LAMP do anything?" in document
    no_heading = report.build(_run(tmp_path / "plain", events, notes="INC adds one (e1)\n"))
    assert "The notes have no Verified section" in report.render(no_heading)


def test_the_ending_follows_the_gates_rules_and_the_runners_ledger(tmp_path):
    def outcome(name: str, events: list[dict], **options: object) -> dict:
        ledger = options.pop("ledger", None)
        run = _run(tmp_path / name, events, **options)  # type: ignore[arg-type]
        if ledger is not None:
            _ledger(run, ledger, {"state": "ended", "outcome": ledger[-1]["ended_because"], "detail": ""})
        return report.build(run)["ending"]

    inc = _event("INC", _grade("change", "change", True), data={"amount": 1})
    won = _event("INC", _grade("win", "win", True), state="WIN", done=1, data={"amount": 2})
    end = outcome("win", [START, inc, won])
    assert (end["outcome"], end["win_at"], end["progress"], end["paid"]) == ("WIN", 2, [1, 1], 2)
    assert outcome("cap", [START, inc, inc], budget={"actions": 2})["outcome"] == "CAP"
    spent = [{"kind": "spend_report", "id": "s1", "usd": 0.06, "tokens": 10}]
    assert outcome("usd", [START, inc], budget={"actions": 9, "usd": 0.05}, activity=spent)["outcome"] == "USD_CAP"
    stop = outcome("stop", [START, inc], notes="STOP: the lamp cannot be lit\n")
    assert (stop["outcome"], stop["declaration"]) == ("SELF_STOP", "the lamp cannot be lit")
    refusal = {"kind": "command_end", "code": "APPROVAL_REQUIRED", "error": "BOMB is approval-gated (default-deny) and has no fresh approval"}
    owner = outcome("owner", [START, inc], activity=[refusal])
    assert owner["outcome"] == "OWNER_NEEDED" and owner["owner_pending"][0]["action"] == "BOMB"
    assert outcome("open", [START, inc])["outcome"] == "UNFINISHED"
    # With the runner's ledger, its last session says why the run stands
    # where it does: an old declaration in the notes is not a new stop.
    session = {"session": 1, "started": STAMP, "ended": STAMP, "ended_because": "UNFINISHED", "launch": {"paid": 0},
               "paid_at_exit": 1, "declaration": None, "owner_pending": []}
    assert outcome("old", [START, inc], notes="STOP: old\n", ledger=[session])["outcome"] == "UNFINISHED"
    declared = {**session, "ended_because": "SELF_STOP", "declaration": "new"}
    end = outcome("new", [START, inc], notes="STOP: old\nSTOP: new\n", ledger=[session, declared])
    assert (end["outcome"], end["declaration"], end["declarations"]) == ("SELF_STOP", "new", ["old", "new"])
    assert end["runner"]["outcome"] == "SELF_STOP"


def test_the_conclusions_and_the_other_declarations(tmp_path):
    events = [
        START,
        _event("BOMB", _grade("ch counter = 0", "channel_eq", True, channel="counter"),
               declares={"worst_case": "the level is lost", "recovery": "assay reset"}),
        _event("NOOP", _grade("noop", "noop", True),
               declares={"impossible": "the counter cannot pass 2", "coverage_audit": "INC exercised at e1"}),
        _event("NOOP", _grade("noop", "noop", True), declares={"conclusion": "give_up: nothing left to try"}),
    ]
    record = report.build(_run(tmp_path, events))
    declared = record["declarations"]
    assert declared["keys"] == {"conclusion": 1, "coverage_audit": 1, "impossible": 1, "recovery": 1, "worst_case": 1}
    assert [(item["event"], item["conclusion"], item["coverage_audit"]) for item in declared["conclusions"]] == [
        (2, {"impossible": "the counter cannot pass 2"}, "INC exercised at e1"),
        (3, {"conclusion": "give_up: nothing left to try"}, None),
    ]
    document = report.render(record)
    assert "- e2 `NOOP`: impossible: the counter cannot pass 2. Coverage audit: INC exercised at e1." in document
    assert "- e3 `NOOP`: conclusion: give\\_up: nothing left to try. Coverage audit: none given." in document


def test_the_document_shows_the_players_text_without_rendering_it(tmp_path):
    notes = "## Verified\n- [a link](http://example.com) <img src=x> `tick` *bold* (e1)\n"
    events = [START, _event("INC", _grade("ch counter = `1`", "channel_eq", False, "read ``2``", channel="counter"),
                            data={"amount": 1, "note": "x" * 60})]
    document = report.render(report.build(_run(tmp_path, events, notes=notes)))
    assert "\\[a link\\](http://example.com) \\<img src=x\\> \\`tick\\` \\*bold\\*" in document
    assert "<img" not in document.replace("\\<img", "")
    assert "`` ch counter = `1` ``" in document and "``` read ``2`` ```" in document
    assert "note=xxxxxxxxxxxxxxxxxxxxx..." in document
    assert report.ids([1, 2, 3, 5, 7, 8, 12], 10) == "e1-e3, e5, e7, e8, e12"
    assert report.ids(range(0, 20, 2), 3) == "e0, e2, e4, and 7 more"
    assert report._fenced("a\n```\nb") == ["````", "a\n```\nb", "````"]


def test_the_command_line_writes_beside_the_run_and_refuses_the_state_directories(tmp_path, capsys):
    run = _run(tmp_path, [START, _event("INC", _grade("change", "change", True), data={"amount": 1})])
    assert cli.main(["report", "--run-dir", str(run)]) == 0
    out = capsys.readouterr().out
    document, data = run.with_name("run.report.md"), run.with_name("run.report.json")
    assert out.startswith(f"WROTE | {document} | {data}\n")
    assert "REPORT | UNFINISHED | progress 0/1 | paid actions 1/40 | CLEAN | held 1, missed 0 | no spend reported" in out
    assert document.read_text().startswith("# ASSAY report: fixture\n")
    assert json.loads(data.read_text())["report_version"] == report.REPORT_VERSION
    elsewhere = tmp_path / "out" / "mine.md"
    assert report.main(["--run-dir", str(run), "--out", str(elsewhere)]) == 0
    assert elsewhere.is_file() and elsewhere.with_suffix(".json").is_file()
    capsys.readouterr()
    for argv, message in [
        (["--out", str(run / ".assay" / "r.md")], "ERROR | --out must not point under the run's .assay"),
        (["--out", str(run / ".claude" / "r.md")], "ERROR | --out must not point under the run's .claude"),
        (["--sessions", str(tmp_path)], "ERROR | no session ledger at"),
        (["--items", "0"], "ERROR | --items must be at least 1"),
    ]:
        assert report.main(["--run-dir", str(run), *argv]) == 2
        assert capsys.readouterr().err.startswith(message)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert report.main(["--run-dir", str(empty)]) == 2
    assert "ERROR | no journal in" in capsys.readouterr().err


def test_an_external_check_is_run_and_recorded(tmp_path, capsys):
    run = _run(tmp_path, [START, _event("INC", _grade("change", "change", True), data={"amount": 1})])
    passing = shlex.join([sys.executable, "-c", "import json, sys; print(json.dumps({'verdict': 'correct', 'run': sys.argv[1]}))", "{run_dir}"])
    record = report.build(run, check=passing)
    assert record["check"]["passed"] is True and record["check"]["result"] == {"verdict": "correct", "run": str(run)}
    document = report.render(record)
    assert "- **The external check:** passed (exit status 0)." in document and '"verdict": "correct"' in document
    failing = shlex.join([sys.executable, "-c", "print('wrong: ``` b1 unpaired'); raise SystemExit(1)"])
    record = report.build(run, check=failing)
    assert (record["check"]["passed"], record["check"]["exit_code"]) == (False, 1)
    document = report.render(record)
    assert "failed (exit status 1)" in document and "````\nwrong: ``` b1 unpaired\n````" in document
    assert report.main(["--run-dir", str(run), "--check", "no-such-checker-anywhere --run {run_dir}"]) == 2
    assert "ERROR | the check `no-such-checker-anywhere` is not on PATH" in capsys.readouterr().err


def test_a_published_journal_reports_the_head_its_pack_published(tmp_path):
    heads = json.loads((REPO / "evidence" / "factorio" / "heads.json").read_text())
    published = {entry["task"]: entry for entry in heads["runs"]}["circuit"]
    run = tmp_path.resolve() / "circuit"
    (run / ".arc").mkdir(parents=True)
    with gzip.open(REPO / "evidence" / "factorio" / "journal-circuit.jsonl.gz", "rb") as source:
        (run / ".arc" / "events.jsonl").write_bytes(source.read())
    record = report.build(run)
    checker = record["integrity"]["checker"]
    assert (checker["chain_head"], checker["verdict"], checker["state_dir"]) == (published["chain_head"], "CLEAN", ".arc")
    assert (record["ending"]["outcome"], record["ending"]["paid"]) == ("WIN", published["paid"])
    assert record["run"]["state_dir"] == ".arc" and record["sessions"] is None and record["notes"] is None
    assert "## Never exercised" in report.render(record)


def test_the_runner_imports_only_the_standard_library_and_itself():
    """tools/eval imports nothing from `assay`, as its README says: the report
    reads files, so it runs where the harness is not installed."""
    import ast

    for path in [*sorted((REPO / "tools" / "eval").glob("*.py")), REPO / "tools" / "eval" / "fake_player"]:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                itself = name == "tools.eval" or name.startswith("tools.eval.")
                assert itself or top == "__future__" or top in sys.stdlib_module_names, f"{path.name} imports {name}"


# ---- end to end ----------------------------------------------------------------------------------

def _env() -> dict[str, str]:
    import os

    env = {key: value for key, value in os.environ.items() if key not in ("ASSAY_OWNER_TOKEN_FILE", "ASSAY_STOP_GATE")}
    return {**env, "ASSAY_PYTHON": sys.executable}


def _assay(run: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(LAUNCHER), "--run-dir", str(run), *args], capture_output=True, text=True,
                          env=_env(), timeout=180)


def test_the_report_of_a_played_run_agrees_with_the_kernel(tmp_path):
    tmp_path = tmp_path.resolve()
    run, token = tmp_path / "run", tmp_path / "run.token"
    run.mkdir()
    started = _assay(run, "start", "counterdemo", "--adapter", f"{COUNTER_WORLD}:factory", "--registry",
                     str(COUNTER_REGISTRY), "--owner-token-file", str(token))
    assert started.returncode == 0, started.stdout + started.stderr
    (run / "PLAYER.md").write_text(
        "Scripted.\n"
        "assay state declare counter --path counter\n"
        'assay act INC amount=1 --predict "ch counter delta = 1"\n'
        'assay act SET_LAMP state=on --predict "ch counter delta = 1"\n'
        'assay act NOOP --predict "something happens"\n'
        "note: STOP: the lamp does not move the counter (e2)\n"
    )
    try:
        played = subprocess.run(
            [sys.executable, "-m", "tools.eval", "session", "--run-dir", str(run), "--assay", str(LAUNCHER),
             "--poll-seconds", "0.1", "--", *FAKE_PLAYER],
            cwd=REPO, env=_env(), capture_output=True, text=True, timeout=240,
        )
        assert played.returncode == 0 and "OUTCOME | SELF_STOP | 1 session(s)" in played.stdout, played.stdout + played.stderr
        status = _assay(run, "status")
        line = next(text for text in status.stdout.splitlines() if text.startswith("PREDICTIONS |"))
        assert _assay(run, "audit").stdout.startswith("AUDIT | CLEAN")
        notes = run / ".assay" / "NOTES.md"
        notes.write_text(notes.read_text().replace("## Verified (cite event ids)\n", "## Verified (cite event ids)\n- INC adds one (e1)\n"))
        done = subprocess.run([sys.executable, "-m", "tools.eval", "report", "--run-dir", str(run)], cwd=REPO,
                              env=_env(), capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stdout + done.stderr
        assert done.stdout.splitlines()[-1] == (
            "REPORT | SELF_STOP | progress 0/1 | paid actions 3/40 | CLEAN | held 1, missed 1 | $0.04 reported to the run"
        )
        record = json.loads(run.with_name("run.report.json").read_text())
        assert record["predictions"]["line"] == line
        chain = json.loads((run / ".assay" / "chain.json").read_text())
        checker = record["integrity"]["checker"]
        assert (checker["verdict"], checker["chain_head"], checker["stored_chain"]) == ("CLEAN", chain["head"], "intact")
        assert record["integrity"]["audit"]["current"] is True and record["integrity"]["audit"]["verdict"] == "CLEAN"
        lines = (run / ".assay" / "events.jsonl").read_text().splitlines()
        ledger = coverage_ledger([Event.from_json(json.loads(text)) for text in lines])
        unit = record["coverage"]["unit"]
        assert (unit["untried"], unit["dead"], unit["paid"]) == (ledger["untried"], ledger["dead"], ledger["paid_on_unit"])
        assert record["coverage"]["run"]["untried"] == ["BOMB"]
        end = record["ending"]
        assert (end["outcome"], end["declaration"]) == ("SELF_STOP", "the lamp does not move the counter (e2)")
        rows = record["sessions"]["rows"]
        assert [(row["session"], row["host"], row["ended_because"], row["paid_from"], row["paid_to"]) for row in rows] == [
            (1, "fake_player", "SELF_STOP", 0, 3)
        ]
        assert record["sessions"]["totals"]["dollars"] == 0.04 and record["activity"]["spend"] == {"usd": 0.04, "tokens": 440, "reports": 1}
        assert record["notes"]["verified"][0]["verdict"] == "held"
        document = run.with_name("run.report.md").read_text()
        for heading in ("## Summary", "## How it ended", "## Integrity", "## What held", "## The misses",
                        "## The player's Verified lines", "## Never exercised", "## The owner's acts and the refusals",
                        "## Sessions and cost"):
            assert heading in document
        assert "- **How it ended:** SELF_STOP, declared: the lamp does not move the counter (e2). Progress 0/1" in document
        assert "- e2 `SET_LAMP state=on`: `ch counter delta = 1` missed; the world: `ch counter moved +0 (1 -> 1)`." in document
        assert "| 1 | fake\\_player Python " in document
        assert "- **Predictions:** 1 outcome held and 1 missed on 3 paid actions, counted as `assay status` counts them; " \
            "1 more left out of that count." in document
    finally:
        stop_run(run)

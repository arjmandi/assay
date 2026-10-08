"""The run model (docs/ARCHITECTURE.md section 6.3): `Run.load` reads once and
never writes, `run.append` is the one writer and keeps the held head equal to
the head over the file, a chain behind by a crash's one line is tolerated and
repaired by the next append, a diverged or malformed chain, a contiguity
problem or a line that does not decode refuses a strict load and is reported
by a lenient one, a sealed anchor file (section 8.3) is reported by a lenient
load and refuses a strict one with RUN_SEALED after the journal's own
problems, the waivers are rebuilt from the activity log, the manifest's
entries are admitted by the pinned registry or a module_installed record
(section 8.2), and `verify_disk` sees an edit, an in-place edit of the
mutation log included."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import event_of, journal_head

from assay.core import AssayError, RunPaths, atomic_json
from assay.run import CHAIN_BEHIND, CHAIN_DIVERGED, CHAIN_INTACT, Run, Tamper


def _run_dir(tmp_path: Path) -> RunPaths:
    paths = RunPaths(tmp_path / "run")
    paths.state.mkdir(parents=True)
    atomic_json(paths.config, {"game_id": "fake1", "mode": "local"})
    atomic_json(paths.registry, {"actions": [{"name": "INC", "params": {}}], "budget": {"actions": 60}})
    return paths


def _events(paths: RunPaths) -> list[dict]:
    return [json.loads(line) for line in paths.events.read_text().splitlines() if line.strip()]


def test_load_requires_a_run(tmp_path):
    with pytest.raises(AssayError, match="not initialized"):
        Run.load(RunPaths(tmp_path), strict=False)


def test_fresh_run_appends_with_held_ids_and_writes_the_chain(tmp_path):
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    assert run.events == [] and run.chain_event == -1 and run.journal_bytes == 0
    assert run.registry is not None and run.registry["budget"] == {"actions": 60}
    assert run.integrity.chain == "absent" and run.integrity.contiguous
    first = run.append(event_of(id=-1, action="START", counts_action=False, level_before=None))
    second = run.append(event_of(id=-1))
    assert (first.id, second.id) == (0, 1)
    assert [event["id"] for event in _events(paths)] == [0, 1]
    assert run.chain_event == 1 and run.chain_head == journal_head(paths)
    assert run.heads[-1] == run.chain_head and len(run.heads) == 2
    assert json.loads((paths.state / "chain.json").read_text()) == {"event_id": 1, "head": run.chain_head}
    assert run.journal_bytes == paths.events.stat().st_size
    assert run.verify_disk() == []
    again = Run.load(paths, strict=True)
    assert again.events == run.events and again.chain_head == run.chain_head
    assert again.integrity.chain == CHAIN_INTACT and again.journal_bytes == run.journal_bytes
    assert again.heads == run.heads
    # The head after each line is the spec's chain over the file's prefix.
    lines = paths.events.read_text().splitlines()
    for index in range(len(lines)):
        prefix = RunPaths(tmp_path / f"prefix{index}")
        prefix.state.mkdir(parents=True)
        prefix.events.write_text("\n".join(lines[: index + 1]) + "\n")
        assert again.heads[index] == journal_head(prefix)


def test_anchors_on_win_and_every_twenty_five(tmp_path, monkeypatch):
    anchors = tmp_path / "anchors"
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(anchors))
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    for _ in range(26):
        run.append(event_of(id=-1))
    run.append(event_of(id=-1, state="WIN"))
    from assay.integrity import anchor_file

    written = [json.loads(line) for line in anchor_file(paths, run.config).read_text().splitlines()]
    assert [entry["event_id"] for entry in written] == [25, 26]
    assert written[-1]["head"] == run.chain_head


def test_a_crash_behind_chain_is_tolerated_and_repaired(tmp_path):
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1))
    behind = (paths.state / "chain.json").read_text()
    run.append(event_of(id=-1))
    (paths.state / "chain.json").write_text(behind)  # the daemon died after the line, before the chain
    loaded = Run.load(paths, strict=True)
    assert loaded.integrity.chain == CHAIN_BEHIND and loaded.integrity.refused is None
    assert loaded.integrity.chain_problem == (
        "chain: stored head at e0 does not match the recomputed journal head at e1"
    )
    assert loaded.chain_head == journal_head(paths)
    loaded.append(event_of(id=-1))
    assert json.loads((paths.state / "chain.json").read_text())["event_id"] == 2
    assert Run.load(paths, strict=False).integrity.chain == CHAIN_INTACT


def test_a_chain_two_lines_behind_is_diverged(tmp_path):
    """A crash leaves exactly one line without its chain; more than one is
    not a crash's doing, whatever the prefix says."""
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1))
    behind = (paths.state / "chain.json").read_text()
    run.append(event_of(id=-1))
    run.append(event_of(id=-1))
    (paths.state / "chain.json").write_text(behind)
    assert Run.load(paths, strict=False).integrity.chain == CHAIN_DIVERGED
    with pytest.raises(AssayError, match="^chain: stored head at e0") as refused:
        Run.load(paths, strict=True)
    assert refused.value.code == "CHAIN_DIVERGED" and refused.value.kind == "invalid"


@pytest.mark.parametrize(
    "stored, expected",
    [
        ("{}", "expected {event_id: int, head: str}, got event_id NoneType and head NoneType"),
        ('{"event_id": "x", "head": "y"}', "got event_id str and head str"),
        ("[1, 2]", "chain.json holds list, not an object"),
        ('{"event_id": true, "head": "y"}', "got event_id bool and head str"),
        ("not json", "chain.json is not valid JSON"),
    ],
)
def test_a_malformed_chain_file_is_diverged_not_behind_or_absent(tmp_path, stored, expected):
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1))
    (paths.state / "chain.json").write_text(stored)
    lenient = Run.load(paths, strict=False)
    assert lenient.integrity.chain == CHAIN_DIVERGED
    assert lenient.integrity.chain_problem is not None and expected in lenient.integrity.chain_problem
    assert lenient.stored_chain is None
    with pytest.raises(AssayError, match="^chain: chain.json") as refused:
        Run.load(paths, strict=True)
    assert refused.value.code == "CHAIN_DIVERGED"
    from assay.integrity import audit

    report = audit(lenient)
    assert report.chain == "DIVERGED" and report.invalid_for_scoring is True
    assert any(expected in problem for problem in report.problems)


def test_a_diverged_chain_refuses_strict_and_is_reported_lenient(tmp_path):
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1))
    run.append(event_of(id=-1))
    lines = paths.events.read_text().splitlines()
    lines[0] = lines[0].replace('"INC"', '"XX"')
    paths.events.write_text("\n".join(lines) + "\n")
    lenient = Run.load(paths, strict=False)
    assert lenient.integrity.chain == CHAIN_DIVERGED
    assert lenient.integrity.chain_problem == (
        "chain: stored head at e1 does not match the recomputed journal head at e1"
    )
    assert len(lenient.events) == 2 and lenient.events[0].action == "XX"
    with pytest.raises(AssayError, match="^chain: stored head at e1 does not match") as refused:
        Run.load(paths, strict=True)
    assert refused.value.code == "CHAIN_DIVERGED"
    assert "nothing is rewritten" in str(refused.value)
    assert paths.events.read_text() == "\n".join(lines) + "\n"


def test_a_contiguity_problem_refuses_strict_and_is_reported_lenient(tmp_path):
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1))
    line = paths.events.read_text()
    paths.events.write_text(line + line)  # the same id twice
    lenient = Run.load(paths, strict=False)
    assert not lenient.integrity.contiguous
    assert lenient.integrity.problem == "event timeline is not contiguous at line 2"
    assert lenient.integrity.chain == CHAIN_BEHIND  # the stored chain's prefix still matches
    with pytest.raises(AssayError, match="^event timeline is not contiguous at line 2") as refused:
        Run.load(paths, strict=True)
    assert refused.value.code == "CHAIN_DIVERGED"


def test_verify_disk_names_what_changed(tmp_path):
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1))
    assert run.verify_disk() == []
    with paths.events.open("a") as handle:
        handle.write(paths.events.read_text().splitlines()[-1] + "\n")
    found = run.verify_disk()
    assert [item.what for item in found] == ["events.jsonl length", "events.jsonl head"]
    assert all(isinstance(item, Tamper) for item in found)
    atomic_json(paths.registry, {"actions": [{"name": "INC", "params": {}}]})
    assert "registry.json" in [item.what for item in run.verify_disk()]


def test_verify_disk_sees_a_same_length_edit_of_the_mutation_log(tmp_path):
    from assay.records import Mutation

    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.record_mutation(Mutation(mutation_id=1, action="INC", data={"amount": 1}, reasoning=None,
                                 observation={"state": "NOT_FINISHED"}, timestamp="t"))
    assert run.verify_disk() == []
    assert run.mutations_bytes == paths.mutations.stat().st_size
    reloaded = Run.load(paths, strict=False)
    assert reloaded.mutations_digest == run.mutations_digest
    text = paths.mutations.read_text()
    paths.mutations.write_text(text.replace('"amount":1', '"amount":2'))  # the same length
    assert paths.mutations.stat().st_size == run.mutations_bytes
    found = run.verify_disk()
    assert [item.what for item in found] == ["mutations.jsonl digest"]
    assert found[0].expected == run.mutations_digest and found[0].found != found[0].expected


def test_mutations_manifest_and_owner_hash_are_held(tmp_path):
    paths = _run_dir(tmp_path)
    from assay.core import append_jsonl

    append_jsonl(
        paths.mutations,
        {"mutation_id": 1, "action": "INC", "data": None, "reasoning": None,
         "observation": {"state": "NOT_FINISHED"}},
    )
    atomic_json(paths.state / "owner.json", {"sha256": "a" * 64, "minted_at": 0})
    atomic_json(paths.state / "modules" / "manifest.json", {"version": 1, "modules": [{"name": "probe"}]})
    run = Run.load(paths, strict=False)
    assert run.mutations[0].mutation_id == 1 and run.mutations[0].timestamp
    assert run.mutations_bytes == paths.mutations.stat().st_size
    assert run.owner_hash == "a" * 64
    assert run.manifest == [{"name": "probe"}]
    assert run.verify_disk() == []


def test_a_malformed_line_is_a_finding_for_the_readers_and_a_refusal_for_a_start(tmp_path):
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1))
    run.append(event_of(id=-1))
    good = paths.events.read_text()
    for bad, reason in (
        ('{"id": 2}\n', "line 3 malformed: missing key 'timestamp'"),
        ('{"id": 2, "timestamp": 5, "action": "INC"}\n', "line 3 malformed: event.timestamp must be a string, got int"),
        ("not json at all\n", "line 3 malformed: Expecting value"),
    ):
        paths.events.write_text(good + bad + good.splitlines()[0] + "\n")
        lenient = Run.load(paths, strict=False)
        assert lenient.integrity.malformed is not None and lenient.integrity.malformed.startswith(reason)
        assert lenient.integrity.refused == lenient.integrity.malformed
        # The events before the line are kept and the journal ends there.
        assert [event.id for event in lenient.events] == [0, 1]
        assert lenient.chain_head == run.chain_head and lenient.journal_bytes == len(good.encode())
        assert lenient.integrity.chain == CHAIN_INTACT
        with pytest.raises(AssayError, match="^line 3 malformed") as refused:
            Run.load(paths, strict=True)
        assert refused.value.code == "CHAIN_DIVERGED"
        assert paths.events.read_text() == good + bad + good.splitlines()[0] + "\n"
    from assay.integrity import audit

    report = audit(Run.load(paths, strict=False))
    assert report.events == 2 and report.invalid_for_scoring is True
    assert report.problems == ("journal: line 3 malformed: Expecting value: line 1 column 1 (char 0)",)


def test_a_byte_that_is_not_utf8_is_a_malformed_line(tmp_path):
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1, action="START", counts_action=False, level_before=None))
    run.append(event_of(id=-1))
    with paths.events.open("ab") as handle:
        handle.write(b"\xff\n")
    lenient = Run.load(paths, strict=False)
    assert len(lenient.events) == 2
    assert lenient.integrity.malformed == (
        "line 3 malformed: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"
    )
    assert lenient.integrity.refused == lenient.integrity.malformed
    with pytest.raises(AssayError, match="^line 3 malformed: 'utf-8' codec") as refused:
        Run.load(paths, strict=True)
    assert refused.value.code == "CHAIN_DIVERGED"
    assert run.verify_disk()[0].what == "events.jsonl length"


def test_verify_disk_sees_the_chain_file_the_owner_file_and_an_unreadable_file(tmp_path):
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1, action="START", counts_action=False, level_before=None))
    assert run.verify_disk() == []
    head = run.chain_head
    atomic_json(paths.state / "chain.json", {"event_id": 0, "head": "0" * 64})
    assert run.verify_disk() == [Tamper("chain.json", f"e0 {head}", "e0 " + "0" * 64)]
    (paths.state / "chain.json").unlink()
    assert run.verify_disk() == [Tamper("chain.json", f"e0 {head}", "absent")]
    atomic_json(paths.state / "chain.json", {"event_id": 0, "head": head})
    assert run.verify_disk() == []
    atomic_json(paths.state / "owner.json", {"sha256": "f" * 64})
    assert run.verify_disk() == [Tamper("owner.json", "absent", "f" * 64)]
    (paths.state / "owner.json").unlink()
    paths.config.write_bytes(b"{not json")
    found = run.verify_disk()
    assert [item.what for item in found] == ["config.json"]
    assert found[0].found.startswith("unreadable: corrupt JSON in ")
    paths.events.write_bytes(paths.events.read_bytes() + b"\xff\n")
    assert [item.what for item in run.verify_disk()] == [
        "events.jsonl length", "events.jsonl head", "config.json",
    ]


def _seal_lines(paths: RunPaths, run: Run) -> list[dict]:
    """The anchor file's records without the stamp append_jsonl adds to
    every line, so the seal compares as the note writes it."""
    from assay.integrity import anchor_file

    target = anchor_file(paths, run.config)
    lines = target.read_text().splitlines() if target.exists() else []
    return [{k: v for k, v in json.loads(line).items() if k != "timestamp"} for line in lines]


def test_a_sealed_anchor_is_a_finding_lenient_and_a_refusal_strict(tmp_path, monkeypatch):
    """Section 8.3: the seal is the held count and head; the lenient load
    reports it after the journal's own problems, the strict load refuses with
    RUN_SEALED naming the file, the audit reads it as the end of the journal,
    and what the journal does against the seal is named: nothing, lines past
    it, or a prefix that no longer matches (where the chain's own problem
    refuses first)."""
    from assay.integrity import anchor_file, audit

    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(tmp_path / "anchors"))
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1, action="START", counts_action=False, level_before=None))
    run.append(event_of(id=-1))
    run.seal("tamper_detected")
    target = anchor_file(paths, run.config)
    assert _seal_lines(paths, run) == [{"event_id": 1, "head": run.chain_head, "seal": "tamper_detected"}]
    what = (
        "anchor file sealed at e1 (tamper_detected): the daemon found the run's files "
        "changed under it and the record ends there"
    )
    lenient = Run.load(paths, strict=False)
    assert lenient.integrity.sealed == what
    assert lenient.integrity.refused == what and lenient.integrity.refused_code == "RUN_SEALED"
    assert lenient.integrity.chain == CHAIN_INTACT and lenient.integrity.contiguous
    with pytest.raises(AssayError, match="^anchor file sealed at e1 \\(tamper_detected\\)") as refused:
        Run.load(paths, strict=True)
    assert refused.value.code == "RUN_SEALED" and refused.value.kind == "invalid"
    assert str(refused.value).endswith("; the run is refused and nothing is rewritten")
    assert refused.value.hint == (
        "the anchor file is the operator's: put back the file the tamper_detected record in "
        f".assay/activity.jsonl names, then remove the sealing line from {target} last; the "
        "record stays as the record of what happened"
    )
    report = audit(lenient)
    assert report.tamper_records == 0 and report.tamper_state is None
    assert report.anchors == "DIVERGED" and report.anchor_count == 0 and report.chain == "intact"
    assert report.invalid_for_scoring is True
    assert report.problems == (
        "anchor: sealed at e1 (tamper_detected): the daemon found the run's files changed under "
        "it and the record ends there; the run is invalid for scoring until the operator removes "
        f"the sealing line from {target}",
    )
    # Lines past the seal: named by the loader and the audit alike.
    lenient.append(event_of(id=-1))
    continued = Run.load(paths, strict=False)
    assert continued.integrity.sealed == (
        what + "; the journal continues past the seal to e2, lines the daemon that sealed it never wrote"
    )
    assert audit(continued).problems[-1] == (
        "anchor: the journal continues past the seal to e2, lines the daemon that sealed it never wrote"
    )
    with pytest.raises(AssayError, match="continues past the seal to e2") as refused:
        Run.load(paths, strict=True)
    assert refused.value.code == "RUN_SEALED"
    # A prefix that no longer matches: the chain's own problem refuses first,
    # and the seal's finding stands beside it.
    lines = paths.events.read_text().splitlines()
    lines[1] = lines[1].replace('"INC"', '"XX"')
    paths.events.write_text("\n".join(lines) + "\n")
    edited = Run.load(paths, strict=False)
    assert edited.integrity.chain == CHAIN_DIVERGED and edited.integrity.refused_code == "CHAIN_DIVERGED"
    assert edited.integrity.sealed == (
        what + "; the journal prefix at e1 no longer matches the sealed head; the journal changed "
        "after it was sealed"
    )
    with pytest.raises(AssayError, match="^chain: stored head at e2") as refused:
        Run.load(paths, strict=True)
    assert refused.value.code == "CHAIN_DIVERGED"
    # The remedy: the sealing line removed from the operator's file.
    target.write_text("")
    assert Run.load(paths, strict=False).integrity.sealed is None


def test_the_seal_is_a_typed_record(tmp_path, monkeypatch):
    """The anchor file's sealing line as a record (section 6.2's convention):
    the three keys the note names, the stamp every line carries left alone,
    a malformed line skipped by `seal_of`."""
    from assay.integrity import Seal, anchor_file, seal_of

    seal = Seal(event_id=4, head="a" * 64, seal="tamper_detected")
    assert Seal.from_json({**seal.to_json(), "timestamp": "t"}) == seal
    assert seal.to_json() == {"event_id": 4, "head": "a" * 64, "seal": "tamper_detected"}
    with pytest.raises(TypeError, match="^seal.event_id must be an integer, got str$"):
        Seal.from_json({"event_id": "4", "head": "a", "seal": "tamper_detected"})
    with pytest.raises(KeyError):
        Seal.from_json({"event_id": 4, "seal": "tamper_detected"})
    assert seal_of([{"seal": "tamper_detected", "event_id": True, "head": "x"}, seal.to_json()]) == seal
    assert seal_of([{"event_id": 1, "head": "x", "run": "r"}]) is None
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(tmp_path / "anchors"))
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1, action="START", counts_action=False, level_before=None))
    run.seal("tamper_detected")
    written = json.loads(anchor_file(paths, run.config).read_text().splitlines()[-1])
    assert Seal.from_json(written) == Seal(event_id=0, head=run.chain_head, seal="tamper_detected")
    assert set(written) == {"event_id", "head", "seal", "timestamp"}


def test_an_unreadable_anchor_file_is_a_finding_lenient_and_a_refusal_strict(tmp_path, monkeypatch):
    """Section 8.3: a line of the anchor file that is not one JSON object is
    never read around. The lenient load names the file and the line, the
    strict load refuses with RECORD_CORRUPT, the audit reads the anchors as
    DIVERGED with a problem line, the ANCHORS line says unreadable, and a
    seal behind the bad line is not looked for until the line is fixed."""
    from assay.integrity import anchor_file, anchor_line, audit

    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(tmp_path / "anchors"))
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1, action="START", counts_action=False, level_before=None))
    run.seal("tamper_detected")
    target = anchor_file(paths, run.config)
    good = target.read_text()
    for bad, problem in (
        ("not json\n", "line 1 is not JSON (Expecting value: line 1 column 1 (char 0))"),
        ("[1, 2]\n", "line 1 is not an object"),
    ):
        target.write_text(bad + good)
        lenient = Run.load(paths, strict=False)
        assert lenient.integrity.anchors_unreadable == f"anchor file {target} is unreadable: {problem}"
        assert lenient.integrity.sealed is None
        assert lenient.integrity.refused == lenient.integrity.anchors_unreadable
        assert lenient.integrity.refused_code == "RECORD_CORRUPT"
        with pytest.raises(AssayError, match=f"^anchor file .* is unreadable: {problem[:12]}") as refused:
            Run.load(paths, strict=True)
        assert refused.value.code == "RECORD_CORRUPT" and refused.value.kind == "internal"
        assert refused.value.hint == (
            "the anchor file is the operator's: every line is one JSON object; fix or remove "
            f"the line in {target} by hand, then start again"
        )
        report = audit(lenient)
        assert report.anchors == "DIVERGED" and report.anchor_count == 0 and report.invalid_for_scoring is True
        assert report.problems == (
            f"anchor: the anchor file {target} is unreadable: {problem}; `assay start` refuses until it is fixed",
        )
        assert anchor_line(paths, run.config) == (
            f"ANCHORS | {target} | unreadable: {problem}; `assay start` refuses until it is fixed"
        )
    # The line fixed: the seal behind it is found again.
    target.write_text(good)
    assert Run.load(paths, strict=False).integrity.refused_code == "RUN_SEALED"
    assert anchor_line(paths, run.config) == (
        f"ANCHORS | {target} | none yet (every 25 events and on WIN) | sealed at e0"
    )


def test_the_earliest_seal_governs_and_a_seal_beyond_the_journal_is_named(tmp_path, monkeypatch):
    from assay.core import append_jsonl
    from assay.integrity import anchor_file, audit, seal_of

    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(tmp_path / "anchors"))
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1, action="START", counts_action=False, level_before=None))
    target = anchor_file(paths, run.config)
    target.parent.mkdir(parents=True, exist_ok=True)
    append_jsonl(target, {"event_id": 7, "head": "x" * 64, "seal": "tamper_detected"})
    append_jsonl(target, {"event_id": 3, "head": "y" * 64, "seal": "tamper_detected"})
    append_jsonl(target, {"event_id": "no", "head": "z", "seal": "tamper_detected"})  # malformed: ignored
    governing = seal_of([json.loads(line) for line in target.read_text().splitlines()])
    assert governing is not None and governing.event_id == 3
    loaded = Run.load(paths, strict=False)
    assert loaded.integrity.sealed == (
        "anchor file sealed at e3 (tamper_detected): the daemon found the run's files changed "
        "under it and the record ends there; the sealed event e3 is beyond the journal, which ends at e0"
    )
    assert audit(loaded).problems[-1] == "anchor: the sealed event e3 is beyond the journal, which ends at e0"
    with pytest.raises(AssayError, match="beyond the journal, which ends at e0") as refused:
        Run.load(paths, strict=True)
    assert refused.value.code == "RUN_SEALED"
    # A plain anchor beside the seals is still checked on its own.
    append_jsonl(target, {"event_id": 0, "head": run.chain_head, "run": str(paths.root)})
    report = audit(Run.load(paths, strict=False))
    assert report.anchors == "DIVERGED" and report.anchor_count == 1
    assert report.problems[0].startswith("anchor: sealed at e3 (tamper_detected)")


def test_a_seal_that_cannot_be_written_is_an_anchor_failed_record(tmp_path, monkeypatch):
    from assay.core import load_jsonl

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a file where the anchor directory should be\n")
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(blocker / "anchors"))
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    run.append(event_of(id=-1, action="START", counts_action=False, level_before=None))
    run.seal("tamper_detected")
    failed = [record for record in load_jsonl(paths.activity) if record["kind"] == "anchor_failed"]
    assert len(failed) == 1 and failed[0]["event"] == 0 and failed[0]["seal"] == "tamper_detected"
    assert Run.load(paths, strict=True).integrity.sealed is None


def test_waivers_are_rebuilt_from_the_activity_log_and_approvals_are_not(tmp_path):
    from assay.core import append_jsonl

    paths = _run_dir(tmp_path)
    append_jsonl(paths.activity, {"kind": "liveness_waived", "action": "inc", "because": "safe"})
    append_jsonl(paths.activity, {"kind": "approval_granted", "action": "FIRE"})
    append_jsonl(paths.activity, {"kind": "liveness_waived", "action": "SIREN", "because": "rehearsed"})
    append_jsonl(paths.activity, {"kind": "liveness_waived", "because": "no action"})  # malformed: ignored
    run = Run.load(paths, strict=True)
    assert run.waivers == {"INC", "SIREN"} and run.approvals == {}
    assert not (paths.state / "approvals.json").exists() and not (paths.state / "waivers.json").exists()


def test_manifest_entries_are_admitted_by_the_registry_or_an_install_record(tmp_path):
    """Section 8.2: the registry names a source by the pinned file's name, a
    module_installed record admits an entry by its hash, and the rest are
    held as unadmitted while the manifest the run holds stays the file as
    written, which verify_disk compares."""
    from assay.core import append_jsonl

    paths = _run_dir(tmp_path)
    atomic_json(
        paths.registry,
        {"actions": [{"name": "INC", "params": {}}], "budget": {"actions": 60}, "modules": ["x/listed.py"]},
    )
    entries = [
        {"name": "listed", "file": "listed.py", "sha256": "a" * 64, "origin": "registry"},
        {"name": "added", "file": "added.py", "sha256": "b" * 64, "origin": "install"},
        {"name": "forged", "file": "forged.py", "sha256": "c" * 64, "origin": "install"},
        {"name": "renamed", "file": "renamed.py", "sha256": "a" * 64, "origin": "registry"},
    ]
    atomic_json(paths.state / "modules" / "manifest.json", {"version": 1, "modules": entries})
    append_jsonl(paths.activity, {"kind": "module_installed", "name": "added", "file": "added.py", "sha256": "b" * 64})
    run = Run.load(paths, strict=True)
    assert run.manifest == entries and run.verify_disk() == []
    assert run.unadmitted == {"forged.py", "renamed.py"}
    from assay.modules import module_inventory

    (paths.state / "modules" / "forged.py").write_text("MODULE = None\n")
    inventory = module_inventory(run)
    assert inventory["listed"] == []
    assert inventory["ignored"] == [
        "forged.py (manifest entry not admitted: neither registered nor installed)",
        "listed.py (listed but missing)",
        "added.py (listed but missing)",
        "renamed.py (listed but missing)",
    ]

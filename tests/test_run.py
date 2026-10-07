"""The run model (docs/ARCHITECTURE.md section 6.3): `Run.load` reads once and
never writes, `run.append` is the one writer and keeps the held head equal to
the head over the file, a crash-behind chain is tolerated and repaired by the
next append, a diverged chain or a contiguity problem refuses a strict load
and is reported by a lenient one, and `verify_disk` sees an edit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import event_of

from assay.core import AssayError, RunPaths, atomic_json
from assay.integrity import compute_chain
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
    assert run.chain_event == 1 and run.chain_head == compute_chain(paths)[1]
    assert json.loads((paths.state / "chain.json").read_text()) == {"event_id": 1, "head": run.chain_head}
    assert run.journal_bytes == paths.events.stat().st_size
    assert run.verify_disk() == []
    again = Run.load(paths, strict=True)
    assert again.events == run.events and again.chain_head == run.chain_head
    assert again.integrity.chain == CHAIN_INTACT and again.journal_bytes == run.journal_bytes


def test_anchors_on_win_and_every_twenty_five(tmp_path, monkeypatch):
    anchors = tmp_path / "anchors"
    monkeypatch.setenv("ASSAY_ANCHOR_DIR", str(anchors))
    paths = _run_dir(tmp_path)
    run = Run.load(paths, strict=True)
    for _ in range(26):
        run.append(event_of(id=-1))
    run.append(event_of(id=-1, state="WIN"))
    from assay.integrity import anchor_file

    written = [json.loads(line) for line in anchor_file(paths).read_text().splitlines()]
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
    assert loaded.chain_head == compute_chain(paths)[1]
    loaded.append(event_of(id=-1))
    assert json.loads((paths.state / "chain.json").read_text())["event_id"] == 2
    assert Run.load(paths, strict=False).integrity.chain == CHAIN_INTACT


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
    with pytest.raises(AssayError, match="CHAIN_DIVERGED") as refused:
        Run.load(paths, strict=True)
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
    with pytest.raises(AssayError, match="CHAIN_DIVERGED \\| event timeline is not contiguous at line 2"):
        Run.load(paths, strict=True)


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


def test_a_malformed_line_is_a_decoding_error_not_a_refusal(tmp_path):
    paths = _run_dir(tmp_path)
    with paths.events.open("a") as handle:
        handle.write('{"id": 0}\n')
    with pytest.raises(KeyError):
        Run.load(paths, strict=False)

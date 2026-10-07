"""The OOLONG pack tooling. The repository ships the two smoke packs whole and
every other pack as a manifest; `build_pack.py fetch` rebuilds a pack from the
manifest's pinned shard and verifies it, `verify` checks a built pack, and the
adapter names the fetch command when a pack with a manifest is not built."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PACKS = REPO / "bench" / "oolong" / "packs"
SHIPPED = ("spam4k", "spam8k")
MANIFEST_ONLY = ("synth128k", "synth1m", "synth4m")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_pack():
    return _load("oolong_build_pack", PACKS / "build_pack.py")


def test_shipped_packs_verify_and_the_others_are_manifests_only(build_pack):
    for packid in SHIPPED:
        assert build_pack.verify(PACKS, packid) == []
    for packid in MANIFEST_ONLY:
        manifest = json.loads((PACKS / f"manifest_{packid}.json").read_text())
        assert manifest["dataset_revision"] == build_pack.PINNED_REVISION
        assert len(manifest["corpus_sha256"]) == 64 and len(manifest["questions_sha256"]) == 64
        assert not (PACKS / f"corpus_{packid}.txt").exists()
        assert not (PACKS / f"questions_{packid}.jsonl").exists()


def test_verify_names_what_is_wrong(build_pack, tmp_path):
    for name in ("corpus_spam4k.txt", "questions_spam4k.jsonl", "manifest_spam4k.json"):
        (tmp_path / name).write_bytes((PACKS / name).read_bytes())
    assert build_pack.verify(tmp_path, "spam4k") == []
    (tmp_path / "corpus_spam4k.txt").write_text("not the corpus")
    (tmp_path / "questions_spam4k.jsonl").unlink()
    problems = build_pack.verify(tmp_path, "spam4k")
    assert any(item.startswith("corpus: sha256") for item in problems)
    assert "questions: questions_spam4k.jsonl is missing" in problems


def test_fetch_rebuilds_from_the_pinned_shard_and_refuses_a_mismatch(build_pack, tmp_path):
    """A tiny shard with the dataset's columns stands in for the hub: build a
    pack from it, keep the manifest alone, fetch it back byte for byte, then
    change the shard and watch fetch delete what it built."""
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    pytest.importorskip("pandas")
    corpus = "Date: Jan 02, 2024 || User: 7 || Instance: hello\nDate: Feb 03, 2024 || User: 8 || Instance: world\n"
    rows = {
        "id": [2, 1],
        "context_window_id": [5, 5],
        "dataset": ["spam", "spam"],
        "question": ["How many?", "Which user?"],
        "task_group": ["counting", "user"],
        "task": ["TASK_TYPE.NUMERIC", "TASK_TYPE.USER"],
        "answer": ["[2]", "['7']"],
        "answer_type": ["ANSWER_TYPE.NUMERIC", "ANSWER_TYPE.USER"],
        "context_len": [4096, 4096],
        "num_labels": [2, 2],
        "context_window_text": [corpus, corpus],
    }
    shard = tmp_path / "tiny-00000-of-00001.parquet"
    pq.write_table(pa.table(rows), shard)
    out = tmp_path / "packs"
    manifest = build_pack.build(shard, "tiny", out, dataset=None, context_len=None, cwid=None)
    assert manifest["n_questions"] == 2 and manifest["context_window_id"] == 5
    built = {name: (out / name).read_bytes() for name in ("corpus_tiny.txt", "questions_tiny.jsonl")}
    assert json.loads(built["questions_tiny.jsonl"].splitlines()[0])["id"] == 1  # sorted by id
    for name in built:
        (out / name).unlink()
    assert build_pack.verify(out, "tiny") != []

    result = build_pack.fetch(out, "tiny", cache=tmp_path / "cache", parquet=shard)
    assert result["n_questions"] == 2
    assert {name: (out / name).read_bytes() for name in built} == built
    assert build_pack.verify(out, "tiny") == []

    changed = dict(rows, context_window_text=[corpus + "extra\n"] * 2)
    pq.write_table(pa.table(changed), shard)
    for name in built:
        (out / name).unlink()
    with pytest.raises(SystemExit, match="does not match its manifest"):
        build_pack.fetch(out, "tiny", cache=tmp_path / "cache", parquet=shard)
    assert not (out / "corpus_tiny.txt").exists() and not (out / "questions_tiny.jsonl").exists()
    assert (out / "manifest_tiny.json").exists()  # the manifest is never rewritten


def test_adapter_names_the_fetch_command_for_a_manifest_only_pack(tmp_path, monkeypatch):
    from assay.core import AssayError

    packs = tmp_path / "packs"
    packs.mkdir()
    (packs / "manifest_synth128k.json").write_bytes((PACKS / "manifest_synth128k.json").read_bytes())
    monkeypatch.setenv("ASSAY_OOLONG_PACKS", str(packs))
    monkeypatch.syspath_prepend(str(REPO / "bench" / "oolong"))
    adapter = _load("oolong_adapter_under_test", REPO / "bench" / "oolong" / "adapter.py")
    with pytest.raises(AssayError, match="fetch synth128k"):
        adapter.factory(tmp_path / "run", {"game_id": "synth128k"})
    with pytest.raises(AssayError, match="build one from a local shard"):
        adapter.factory(tmp_path / "run", {"game_id": "nopack"})

"""A second real world beyond the test counter: the OOLONG spam4k pack through
the real CLI, daemon and bench adapter. Pure Python, no network, no model: the
corpus is a file, spans are checked verbatim in code, and the sealed scorer
runs at finalize."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from conftest import run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
ADAPTER = REPO / "bench" / "oolong" / "adapter.py"
REGISTRY = REPO / "bench" / "oolong" / "registry_40.json"
CORPUS = REPO / "bench" / "oolong" / "packs" / "corpus_spam4k.txt"
SPAN = "Hello from Orange."


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _events(run: Path) -> list[dict]:
    lines = (run / ".assay" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_oolong_spam4k_to_win_and_sealed_score(tmp_path):
    assert SPAN in CORPUS.read_text()
    run = tmp_path / "spam4k"
    run.mkdir()
    try:
        started = run_cli(
            run, "start", "spam4k", "--adapter", f"{ADAPTER}:factory",
            "--registry", str(REGISTRY),
        )
        assert started.returncode == 0, started.stderr
        assert "REGISTRY | 2 registered actions" in started.stdout
        assert '"question_count": 5' in started.stdout
        assert (run / ".assay" / "corpus.txt").read_text() == CORPUS.read_text()
        assert run_cli(run, "channel", "declare", "banked", "--path", "banked_count").returncode == 0
        # A span that is not in the corpus is refused by the world and the
        # attempt is journaled as evidence (the spend is real).
        refused = run_cli(
            run, "act", "BANK_FACT", f"text={_b64('x')}", f"span={_b64('not in the corpus')}",
            "--predict", "ch banked delta = 0",
        )
        assert refused.returncode == 0 and "OUTCOME | PREDICTED" in refused.stdout
        assert _events(run)[-1]["observation"]["last_result"]["status"] == "refused"
        # The census: a SUBMIT without a banked fact for this question is
        # refused by the world, visible in the observation.
        for number in range(1, 6):
            banked = run_cli(
                run, "act", "BANK_FACT", f"text={_b64('an orange message')}", f"span={_b64(SPAN)}",
                "--predict", "ch banked delta = 1",
            )
            assert banked.returncode == 0 and "OUTCOME | PREDICTED" in banked.stdout, banked.stdout
            claim = "win; level+1" if number == 5 else "level+1"
            submitted = run_cli(
                run, "act", "SUBMIT", f"answer={_b64('Answer: 1')}",
                f"spans={_b64(json.dumps([SPAN]))}", "--predict", claim,
            )
            assert submitted.returncode == 0, submitted.stderr
            assert "OUTCOME | " in submitted.stdout
            assert _events(run)[-1]["levels_completed"] == number
        final = _events(run)[-1]
        assert final["state"] == "WIN" and final["win_levels"] == 5
        score = run / ".assay" / "oolong_score.json"
        assert score.exists() and (run / ".assay" / "oolong_score.md").exists()
        report = json.loads(score.read_text())
        assert report["aggregate"]["scored"] == 5
        assert report["evidence"]["banked_facts"] == 5 and report["evidence"]["refusals"] == 1
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout and "paid 11" in audited.stdout
        assert "chain intact" in audited.stdout
    finally:
        stop_run(run)


def test_oolong_spam4k_batch_mode_to_win(tmp_path):
    """The E5 variant (`control.bank_mode: batch`): several spans per
    BANK_FACT, a plain-text SUBMIT cited by the banked spans, the same census
    and sealed scoring."""
    corpus = CORPUS.read_text()
    second = "Date: Jun 19, 2023 || User: 16544"
    assert SPAN in corpus and second in corpus
    registry = json.loads((REPO / "bench" / "oolong" / "registry_200_batch.json").read_text())
    registry["budget"]["actions"] = 40
    (tmp_path / "reg.json").write_text(json.dumps(registry))
    run = tmp_path / "spam4k"
    run.mkdir()
    try:
        started = run_cli(
            run, "start", "spam4k", "--adapter", f"{ADAPTER}:factory",
            "--registry", str(tmp_path / "reg.json"),
        )
        assert started.returncode == 0, started.stderr
        assert '"bank_mode": "batch"' in started.stdout
        assert run_cli(run, "channel", "declare", "banked", "--path", "banked_count").returncode == 0
        # One missing span refuses the whole batch: nothing banked, the spend journaled.
        refused = run_cli(
            run, "act", "BANK_FACT", f"spans={_b64(json.dumps([SPAN, 'not in the corpus']))}",
            "--predict", "ch banked delta = 0",
        )
        assert refused.returncode == 0 and "OUTCOME | PREDICTED" in refused.stdout, refused.stdout
        result = _events(run)[-1]["observation"]["last_result"]
        assert result["status"] == "refused" and "none of the 2 banked" in result["detail"]
        # A SUBMIT before any bank is refused by the census, in plain text.
        early = run_cli(run, "act", "SUBMIT", "answer=1", "--predict", "level+1")
        assert early.returncode == 0 and "OUTCOME | SURPRISE" in early.stdout
        assert _events(run)[-1]["observation"]["last_result"]["reason"] == "census"
        for number in range(1, 6):
            banked = run_cli(
                run, "act", "BANK_FACT", f"spans={_b64(json.dumps([SPAN, second]))}",
                "--predict", "ch banked delta = 2",
            )
            assert banked.returncode == 0 and "OUTCOME | PREDICTED" in banked.stdout, banked.stdout
            claim = "win; level+1" if number == 5 else "level+1"
            answer = "February_2022" if number == 3 else "1"
            submitted = run_cli(run, "act", "SUBMIT", f"answer={answer}", "--predict", claim)
            assert submitted.returncode == 0 and "OUTCOME | " in submitted.stdout, submitted.stdout
            assert _events(run)[-1]["levels_completed"] == number
        final = _events(run)[-1]
        assert final["state"] == "WIN" and final["observation"]["banked_count"] == 10
        report = json.loads((run / ".assay" / "oolong_score.json").read_text())
        assert report["aggregate"]["scored"] == 5
        assert report["evidence"]["banked_facts"] == 10 and report["evidence"]["refusals"] == 2
        submitted_answers = [item["submitted"] for item in report["per_question"]]
        assert "February 2022" in submitted_answers  # the underscore became a space
        audited = run_cli(run, "audit")
        assert "AUDIT | CLEAN" in audited.stdout and "paid 12" in audited.stdout
    finally:
        stop_run(run)

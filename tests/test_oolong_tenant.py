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

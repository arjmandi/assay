"""The hidden-grammar pack (worlds/recipes/hidden-grammar): a seed draws one
language, the examples and the held-out sentences agree with the oracle, the
world refuses what a speaker would not answer and stops at its cap, the
checker tells a classifier that holds every rule from one that misses one, and
a run goes through the real CLI and daemon, with a resume that replays the
answers, to a classifier the checker scores."""

from __future__ import annotations

import collections
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from conftest import run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "worlds" / "recipes" / "hidden-grammar"
SCORE = PACK / "checks" / "score.py"

sys.path.insert(0, str(PACK))
import grammar  # noqa: E402

# Every pack's checker is checks/score.py: load this one by its path, under a
# name of its own, so another pack's checker never answers for it.
_spec = importlib.util.spec_from_file_location("hidden_grammar_score", SCORE)
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)


def _classifier(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(f"import sys\nsys.path.insert(0, {str(PACK)!r})\nimport grammar\n\n{body}")
    return path


def test_a_seed_draws_one_language_and_its_sentences_agree_with_the_oracle():
    assert grammar.language(3) == grammar.language(3) and grammar.language(3) != grammar.language(4)
    assert {(grammar.language(seed).order, grammar.language(seed).adjective) for seed in range(12)} == {
        ("SVO", "before"), ("SVO", "after"), ("SOV", "before"), ("SOV", "after"),
    }
    for seed in (1, 2, 3):
        assert all(grammar.accepts(seed, sentence) for sentence in grammar.examples(seed))
        sentences = grammar.held_out(seed)
        assert sentences == grammar.held_out(seed) and len({text for text, _, _ in sentences}) == 400
        assert collections.Counter(kind for _, _, kind in sentences) == {
            "acceptable": 200, "order": 40, "adjective": 40, "negation": 40, "agreement": 40, "transitivity": 40,
        }
        for text, ok, kind in sentences:
            assert grammar.accepts(seed, text) is ok
            assert ok or kind in grammar.violated(seed, text), (seed, text, kind)


def test_the_world_answers_records_and_stops_at_its_cap(tmp_path):
    grammar.setup(tmp_path, 2)
    lexicon = (tmp_path / grammar.LEXICON).read_text()
    lang = grammar.language(2)
    assert all(word in lexicon for word in lang.words)
    assert lang.nouns[0] + lang.plural not in lexicon.split()
    example = grammar.examples(2)[0]
    assert grammar.ask(tmp_path, 2, {"sentence": example})["acceptable"] is True
    assert grammar.ask(tmp_path, 2, {"sentence": " ".join(reversed(example.split()))})["acceptable"] is False
    for refused in ({"sentence": "hello, world"}, {"sentence": "a " * 13}, {"sentence": "x1"}, {}):
        assert grammar.ask(tmp_path, 2, refused)["status"] == "refused", refused
    assert grammar.observe(tmp_path) == {
        "asked": 2, "asks_left": 38, "accepted": 1, "rejected": 1,
        "last_sentence": " ".join(reversed(example.split())), "last_ok": False,
    }
    for _ in range(38):
        assert grammar.ask(tmp_path, 2, {"sentence": example})["status"] == "ok"
    assert grammar.ask(tmp_path, 2, {"sentence": example})["detail"].startswith("the speaker has answered 40 questions")
    assert grammar.observe(tmp_path)["asks_left"] == 0


def test_the_checker_tells_every_rule_from_all_but_one(tmp_path):
    perfect = _classifier(tmp_path, "perfect", "def accept(sentence):\n    return grammar.accepts(2, sentence)\n")
    assert score.score(2, perfect)["verdict"] == "correct" and score.score(2, perfect)["accuracy"] == 1.0
    everything = _classifier(tmp_path, "everything", "def accept(sentence):\n    return True\n")
    assert score.score(2, everything)["accuracy"] == 0.5
    # Agreement ignored: the suffix may come or go, every other rule holds.
    loose = _classifier(tmp_path, "loose", (
        "lang = grammar.language(2)\n"
        "LOOSE = {grammar.realize(lang, c, agree=a) for c in grammar.clauses(lang) for a in (True, False)}\n\n"
        "def accept(sentence):\n    return sentence in LOOSE\n"
    ))
    result = score.score(2, loose)
    assert result["verdict"] == "wrong" and result["accuracy"] == 0.9
    assert result["by_kind"]["agreement"] == 0.0 and result["by_kind"]["order"] == 1.0
    assert score.score(2, tmp_path / "absent.py")["verdict"] == "missing"
    broken = _classifier(tmp_path, "broken", "def accept(sentence):\n    raise ValueError('no')\n")
    crashed = score.score(2, broken)
    assert crashed["verdict"] == "wrong" and crashed["detail"].startswith("the classifier failed: ValueError")
    asked = tmp_path / "asked.tsv"
    agreement_only = next(text for text, ok, kind in grammar.held_out(2) if kind == "agreement")
    asked.write_text(f"{grammar.examples(2)[0]}\tacceptable\n{agreement_only}\tnot acceptable\n")
    coverage = score.score(2, perfect, asked=asked)["coverage"]
    assert coverage["asked"] == 2 and coverage["tested"]["agreement"] == 1
    assert coverage["never_tested"] == ["order", "adjective", "negation", "transitivity"]


def test_a_run_through_the_cli_replays_its_answers_and_scores(tmp_path):
    run = tmp_path / "grammar"
    run.mkdir()
    start = ["start", "grammar1", "--adapter", f"{PACK / 'adapter.py'}:factory", "--registry", str(PACK / "registry.json"),
             "--seed", "2"]
    try:
        started = run_cli(run, *start, "--owner-token-file", str(tmp_path / "grammar.token"))
        assert started.returncode == 0, started.stderr
        assert run_cli(run, "state", "declare", "last_ok", "--path", "last_ok").returncode == 0
        example = grammar.examples(2)[1]
        backwards = " ".join(reversed(example.split()))
        for sentence, predict in ((example, "ch last_ok = true"), (backwards, "ch last_ok = true")):
            acted = run_cli(run, "act", "ASK", "--params", json.dumps({"sentence": sentence}), "--predict", predict)
            assert acted.returncode == 0, acted.stderr
        assert "RESULT | SURPRISE | prediction missed: ch last_ok = true | ch last_ok = false" in acted.stdout
        asked = run / ".assay" / "world" / "asked.tsv"
        before = asked.read_text()
        assert before == f"{example}\tacceptable\n{backwards}\tnot acceptable\n"
        assert run_cli(run, "stop").returncode == 0
        resumed = run_cli(run, *start)
        assert resumed.returncode == 0 and "RECOVERED | grammar1 | local simulator | replayed 2 paid actions" in resumed.stdout
        assert asked.read_text() == before
        (run / "accept.py").write_text(
            f"import sys\nsys.path.insert(0, {str(PACK)!r})\nimport grammar\n\n"
            "def accept(sentence):\n    return grammar.accepts(2, sentence)\n"
        )
        scored = subprocess.run([sys.executable, str(SCORE), "--run", str(run)], capture_output=True, text=True, timeout=120)
        assert scored.returncode == 0, scored.stdout + scored.stderr
        assert scored.stdout.startswith("SCORE | correct | accuracy 1.000 on 400 held-out sentences")
        never = [kind for kind in grammar.KINDS if kind not in grammar.violated(2, backwards)]
        assert f"ASKED | 2 sentences | rules never tested alone: {', '.join(never)}" in scored.stdout
    finally:
        stop_run(run)

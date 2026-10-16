"""The hidden-grammar checker: a classifier against held-out sentences of the
seed's language. It imports nothing from the harness, so it scores any arm of
a comparison the same way, a run under ASSAY or a baseline that asked its own
copy of the oracle.

    python3 checks/score.py --run RUN_DIR              the seed from .assay/config.json, RUN_DIR/accept.py
    python3 checks/score.py --seed S --classifier FILE  any classifier
    add --asked FILE (an asked.tsv) for what the arm asked, --size N, --json

A classifier is a Python file defining accept(sentence: str) -> bool. It
runs in its own interpreter, with a time limit, over 400 held-out sentences
(by default): half acceptable, half breaking exactly one of the five rules,
the rules equally. The verdict is correct at an accuracy of 0.95 or more,
wrong below it, and missing without a classifier. The breakdown says which
rules the classifier gets wrong, and, with --asked, which rules the arm never
tested with a sentence that broke only that rule. The exit status is 0 for
correct and 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grammar import KINDS, held_out, normalize, violated  # noqa: E402

PASS_ACCURACY = 0.95
TIMEOUT_SECONDS = 60
RUNNER = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("classifier", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sentences = json.loads(sys.stdin.read())
print(json.dumps([bool(module.accept(sentence)) for sentence in sentences]))
"""


def classify(classifier: Path, sentences: list[str]) -> list[bool] | str:
    """The classifier's answers, or the reason it gave none."""
    with tempfile.TemporaryDirectory(prefix="grammar-score-") as scratch:
        runner = Path(scratch) / "run_classifier.py"
        runner.write_text(RUNNER)
        try:
            done = subprocess.run(
                [sys.executable, "-I", str(runner), str(classifier)], input=json.dumps(sentences),
                capture_output=True, text=True, timeout=TIMEOUT_SECONDS, cwd=scratch, check=False,
            )
        except subprocess.TimeoutExpired:
            return f"the classifier ran longer than {TIMEOUT_SECONDS} seconds"
    if done.returncode != 0:
        return "the classifier failed: " + (done.stderr.strip().splitlines() or ["no output"])[-1][:300]
    try:
        answers = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return "the classifier printed no answers"
    if not isinstance(answers, list) or len(answers) != len(sentences):
        return "the classifier answered a different number of sentences"
    return [bool(answer) for answer in answers]


def asked_coverage(seed: int, asked: Path) -> dict[str, Any]:
    """What the arm asked: how many sentences, and per rule how many broke
    only that rule."""
    rows = [line.rsplit("\t", 1)[0] for line in asked.read_text().splitlines() if "\t" in line]
    tested = {kind: 0 for kind in KINDS}
    for sentence in rows:
        kinds = violated(seed, normalize(sentence))
        if len(kinds) == 1:
            tested[kinds[0]] += 1
    return {"asked": len(rows), "tested": tested, "never_tested": [kind for kind, count in tested.items() if not count]}


def score(seed: int, classifier: Path | None, *, size: int = 400, asked: Path | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"seed": seed, "classifier": str(classifier) if classifier else None, "size": size}
    if asked is not None and asked.is_file():
        result["coverage"] = asked_coverage(seed, asked)
    if classifier is None or not classifier.is_file():
        return {**result, "verdict": "missing", "detail": "no classifier file", "accuracy": 0.0}
    sentences = held_out(seed, size)
    answers = classify(classifier, [sentence for sentence, _, _ in sentences])
    if isinstance(answers, str):
        return {**result, "verdict": "wrong", "detail": answers, "accuracy": 0.0}
    by_kind: dict[str, list[int]] = {}
    right = 0
    for (sentence, ok, kind), answer in zip(sentences, answers):
        slot = by_kind.setdefault(kind, [0, 0])
        slot[1] += 1
        if answer == ok:
            right += 1
            slot[0] += 1
    accuracy = round(right / len(sentences), 4)
    return {
        **result,
        "verdict": "correct" if accuracy >= PASS_ACCURACY else "wrong",
        "accuracy": accuracy,
        "by_kind": {kind: round(hit / total, 4) for kind, (hit, total) in sorted(by_kind.items())},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="score a hidden-grammar classifier on held-out sentences")
    parser.add_argument("--run", type=Path, help="a run directory: the seed from .assay/config.json, the classifier its accept.py")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--classifier", type=Path)
    parser.add_argument("--asked", type=Path, help="an asked.tsv: what the arm asked the oracle")
    parser.add_argument("--size", type=int, default=400)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.run is not None:
        config = json.loads((args.run / ".assay" / "config.json").read_text())
        seed = int(config.get("seed") or 0)
        classifier = args.classifier or args.run / "accept.py"
        asked = args.asked or args.run / ".assay" / "world" / "asked.tsv"
    elif args.seed is not None:
        seed, classifier, asked = args.seed, args.classifier, args.asked
    else:
        parser.error("give --run, or --seed with --classifier")
    result = score(seed, classifier.resolve() if classifier else None, size=args.size, asked=asked)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"SCORE | {result['verdict']} | accuracy {result['accuracy']:.3f} on {args.size} held-out sentences")
        for kind, value in result.get("by_kind", {}).items():
            print(f"KIND | {kind} | {value:.3f}")
        if "coverage" in result:
            never = ", ".join(result["coverage"]["never_tested"]) or "none"
            print(f"ASKED | {result['coverage']['asked']} sentences | rules never tested alone: {never}")
    return 0 if result["verdict"] == "correct" else 1


if __name__ == "__main__":
    raise SystemExit(main())

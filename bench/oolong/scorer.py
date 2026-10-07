"""Sealed OOLONG scoring: reuse the authors' scorer verbatim, offline.

At `finalize()` the OOLONG adapter scores every SEALED SUBMIT answer against the
gold answer using OOLONG's own scoring functions (RESEARCH.md §1.3): exact match
for labels/comparisons/users, `0.75**|gold-pred|` partial credit for numbers,
`strptime` gold + parsed output for dates. The scoring code is the vendored,
byte-identical `vendor/oolong_eval_helpers.py` (see vendor/PROVENANCE.md).

The daemon runtime that serves the broker carries only numpy + pillow, so the
scorer's heavy / paid imports (`litellm`, `datasets`, `tiktoken`,
`transformers`, `jsonlines`) are stubbed and `dateutil` is shimmed when absent,
the exact isolation the M0 spike proved (`oolong-spike/scorer_isolation.py`).
Nothing here touches the network, an API key, or a model.

PARITY NOTE: answers are fed in OOLONG's canonical ``"Answer: {x}"`` form so the
upstream lenient free-text parser (`synth_attempt_answer_parse`) runs the exact
path the paper uses. If python-dateutil is not importable in the daemon runtime,
DATE model-output parsing falls back to a small format shim (gold parsing is
stdlib `strptime` and is unaffected); install python-dateutil for full DATE
parity. NUMERIC / LABEL / COMPARISON / USER scoring is unaffected either way.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

VENDOR = Path(__file__).resolve().parent / "vendor" / "oolong_eval_helpers.py"
SCORER_MODEL_TAG = "assay-oolong-m1"


def _install_stub_dependencies() -> dict[str, bool]:
    """Stub the scorer's heavy / paid deps and shim dateutil. Idempotent.

    Returns a small provenance dict recording whether real dateutil was found
    (full DATE parity) or the shim was installed.
    """
    for name in ("litellm", "tiktoken", "jsonlines"):
        sys.modules.setdefault(name, types.ModuleType(name))
    if "datasets" not in sys.modules:
        datasets = types.ModuleType("datasets")
        datasets.load_dataset = None  # referenced at import, never called in scoring
        sys.modules["datasets"] = datasets
    if "transformers" not in sys.modules:
        transformers = types.ModuleType("transformers")
        transformers.AutoTokenizer = object  # referenced, never called in scoring
        sys.modules["transformers"] = transformers

    try:  # full parity when python-dateutil is available
        import dateutil.parser  # noqa: F401

        return {"dateutil": "real"}
    except Exception:  # noqa: BLE001 - install a minimal shim otherwise
        pass

    import datetime as _dt

    def _parse(text: str, *args: Any, **kwargs: Any) -> _dt.datetime:
        raw = str(text).strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y"):
            try:
                return _dt.datetime.strptime(raw, fmt)
            except ValueError:
                continue
        raise ValueError(f"dateutil shim cannot parse {raw!r}")

    parser_mod = types.ModuleType("dateutil.parser")
    parser_mod.parse = _parse
    dateutil_mod = types.ModuleType("dateutil")
    dateutil_mod.parser = parser_mod
    sys.modules["dateutil"] = dateutil_mod
    sys.modules["dateutil.parser"] = parser_mod
    return {"dateutil": "shim"}


def load_eval_helpers() -> Any:
    """Import the vendored OOLONG scorer with all heavy deps stubbed/shimmed."""
    _install_stub_dependencies()
    spec = importlib.util.spec_from_file_location("oolong_eval_helpers", VENDOR)
    if spec is None or spec.loader is None:  # pragma: no cover - vendor missing
        raise RuntimeError(f"cannot load vendored OOLONG scorer at {VENDOR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def vendor_sha256() -> str:
    return hashlib.sha256(VENDOR.read_bytes()).hexdigest()


def canonical_output(answer: str) -> str:
    """Feed the agent's answer in OOLONG's canonical ``Answer: {x}`` form."""
    return f"Answer: {answer}"


def score_submissions(
    submissions: list[dict[str, Any]],
    questions_by_id: dict[int, dict[str, Any]],
    *,
    split: str = "synth",
    model_tag: str = SCORER_MODEL_TAG,
) -> dict[str, Any]:
    """Score every sealed SUBMIT against gold with the reused OOLONG scorer.

    `submissions`: sealed records, each {"question_id", "answer", "spans",
    "event_id"?}. `questions_by_id`: question_id -> gold datapoint (must carry
    answer, answer_type, id, context_window_id, dataset). Correctness is computed
    HERE and only here, never mid-run.
    """
    provenance = _install_stub_dependencies()
    helpers = load_eval_helpers()
    process = (
        helpers.synth_process_response
        if split == "synth"
        else helpers.dnd_process_response
    )

    per_question: list[dict[str, Any]] = []
    total = 0.0
    exact_hits = 0
    by_group: dict[str, list[float]] = {}
    by_type: dict[str, list[float]] = {}
    for record in submissions:
        qid = int(record["question_id"])
        datapoint = questions_by_id.get(qid)
        answer = str(record.get("answer", ""))
        if datapoint is None:  # pragma: no cover - pack/journal mismatch
            per_question.append(
                {"question_id": qid, "score": 0.0, "error": "question not in pack"}
            )
            continue
        output = canonical_output(answer)
        scored = process(datapoint, output, model_tag)
        score = float(scored["score"])
        total += score
        if score == 1.0:
            exact_hits += 1
        group = str(datapoint.get("task_group", "?"))
        atype = str(datapoint.get("answer_type", "?"))
        by_group.setdefault(group, []).append(score)
        by_type.setdefault(atype, []).append(score)
        per_question.append(
            {
                "question_id": qid,
                "event_id": record.get("event_id"),
                "task_group": group,
                "answer_type": atype,
                "submitted": answer,
                "submitted_canonical": output,
                "parsed": scored.get("attempted_parse"),
                "gold": scored.get("answer"),
                "score": score,
                "spans_cited": len(record.get("spans") or []),
            }
        )

    n = len(per_question)
    scored_n = sum(1 for q in per_question if "error" not in q)

    def _mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 6) if values else 0.0

    return {
        "benchmark": "oolong",
        "split": split,
        "scorer": {
            "source": "vendored OOLONG eval_helpers (reused verbatim)",
            "function": "synth_process_response" if split == "synth" else "dnd_process_response",
            "vendor_sha256": vendor_sha256(),
            "answer_form": "Answer: {x}",
            "dateutil": provenance["dateutil"],
            "network": "none",
        },
        "aggregate": {
            "questions": n,
            "scored": scored_n,
            "mean_score": round(total / scored_n, 6) if scored_n else 0.0,
            "sum_score": round(total, 6),
            "exact_hits": exact_hits,
            "by_task_group": {k: _mean(v) for k, v in sorted(by_group.items())},
            "by_answer_type": {k: _mean(v) for k, v in sorted(by_type.items())},
        },
        "per_question": per_question,
    }

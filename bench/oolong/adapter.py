"""OOLONG corpus-pack world adapter for the ASSAY broker (query-framed, M1).

Serves ONE OOLONG corpus + its question set as an ASSAY session (RESEARCH.md
§6.1, §7). ASSAY refuses to play the long-context game: the corpus registers as
a DOCUMENT OBSERVER written to a file in the run directory, and the agent reads
it with its own offline tools FOR FREE; the model's window never holds the
corpus. The observation carries only the corpus path, its size/shape, and the
CURRENT question, never the corpus body.

    assay start spam4k \
        --adapter <repo>/bench/oolong/adapter.py:factory \
        --registry <repo>/bench/oolong/registry_40.json

Which pack loads: the ASSAY game id (`config["game_id"]`), overridable with
ASSAY_OOLONG_PACK; the packs directory is ASSAY_OOLONG_PACKS or the adapter's
own `packs/`. A pack is `corpus_{id}.txt` + `questions_{id}.jsonl` + a manifest.
The smoke packs ship whole; the others ship as manifests and are rebuilt on
first use by `packs/build_pack.py fetch <id>`.

Two PAID actuators, both grounded in code (the referee grades EVIDENCE
INTEGRITY, since a static corpus has no world-response to grade a prediction
against, RESEARCH.md §6.1). Their arguments are plain strings and an array of
strings, passed as JSON (`assay act NAME --params '{...}'`), since a span
holds spaces; the runs recorded before 1.2.0 carried them base64-encoded:

    BANK_FACT --params '{"text": "...", "span": "..."}'
        commit a fact citing a corpus span; the span is verified as a verbatim
        substring (str.find) IN CODE. A span not present is REFUSED and the
        attempt is journaled (evidence, like an FLE policy refusal).

    SUBMIT --params '{"answer": "...", "spans": ["...", ...]}'
        answer the current question, citing verbatim spans (each checked). Also
        COVERAGE-gated by the v1 census (see CENSUS_RULE). On acceptance the
        answer is recorded SEALED (stored, never scored or revealed mid-run)
        and the question pointer advances.

Host progress: `win_levels` = number of questions in the pack, `levels_completed`
= questions accepted through the gate, state WIN when all are submitted with the
census clean. Correctness is computed ONLY at `finalize()`, by the reused OOLONG
scorer, and written to the run dir; the answer key never enters the run before
then.

Determinism: corpus load and span checks are pure (no network / time / random at
run time), so the append-only journal replays to identical observations. The
dataset revision is pinned in each pack's manifest.

Banking modes, chosen by `control.bank_mode` in the run's pinned registry (the
kernel journals the control block and leaves it to the world):

    single (default)  one span per BANK_FACT, an answer and its spans on SUBMIT,
                      exactly as above (the form every published OOLONG run was
                      recorded under, base64-encoded then).
    batch             the E5 variant. BANK_FACT --params '{"spans": [...]}' banks
                      several verbatim spans in one paid action (one missing span
                      refuses the whole action, journaled). SUBMIT answer=<string>
                      is cited by the spans banked for the current question; a
                      spans array, if the registry gives one, is checked verbatim
                      as in single mode. The census and the sealed scoring are the
                      same code.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from assay.core import AssayError

_HERE = Path(__file__).resolve().parent

ACTIONS = ("BANK_FACT", "SUBMIT")
BANK_MODES = ("single", "batch")
SPAN_PREVIEW = 80  # chars of a span echoed back in the observation
LAST_RESULT_STATUSES = ("accepted", "refused", "sealed")

# --- v1 census -------------------------------------------------------------
# The coverage gate on SUBMIT. Kept deliberately SIMPLE and honest for M1
# (RESEARCH.md §6.1 calls the full mechanism an "unread-region ledger"; that is
# the planned refinement, NOT this): a submission is refused unless the agent has
# grounded at least one fact in the corpus for the question it is answering.
CENSUS_MIN_BANKS_PER_QUESTION = 1
CENSUS_RULE = (
    "v1: SUBMIT requires >=1 verified BANK_FACT for the CURRENT question "
    "(reset after each accepted SUBMIT), and every SUBMIT span verbatim in the "
    "corpus. Planned: an unread-region ledger gating on per-question required "
    "scope coverage (RESEARCH.md §6.1)."
)


def _packs_dir() -> Path:
    configured = os.getenv("ASSAY_OOLONG_PACKS")
    return Path(configured).expanduser().resolve() if configured else _HERE / "packs"


def _bank_mode(root: Path) -> str:
    """`control.bank_mode` of the run's pinned registry, `single` when absent."""
    try:
        registry = json.loads((Path(root) / ".assay" / "registry.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return "single"
    control = registry.get("control") if isinstance(registry, Mapping) else None
    mode = str((control or {}).get("bank_mode", "single")).lower()
    if mode not in BANK_MODES:
        raise AssayError(f"control.bank_mode must be one of {list(BANK_MODES)}, got {mode!r}")
    return mode


def _text(raw: Any, field: str) -> str:
    """A string parameter as the registry validated it, non-empty; a value
    with spaces travels as JSON (`--params`), never as a token."""
    if not isinstance(raw, str) or not raw:
        raise AssayError(f"{field} must be a non-empty string (pass it with --params)")
    return raw


def _spans(raw: Any) -> list[str]:
    """The `spans` parameter: a non-empty array of non-empty strings, as the
    registry's schema admits it (`--params '{"spans": ["...", ...]}'`)."""
    if not isinstance(raw, list) or not raw or not all(
        isinstance(item, str) and item for item in raw
    ):
        raise AssayError("spans must be a non-empty array of non-empty strings (pass it with --params)")
    return list(raw)


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union of half-open [start, end) intervals, sorted and merged."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


class OolongSession:
    """One OOLONG corpus pack, served to ASSAY as a general (non-grid) session."""

    def __init__(self, root: Path, config: Mapping[str, Any]):
        self._root = Path(root)
        packs = _packs_dir()
        pack_id = str(os.getenv("ASSAY_OOLONG_PACK") or config.get("game_id", "")).strip()
        if not pack_id:
            raise AssayError("no OOLONG pack id; pass a game id or set ASSAY_OOLONG_PACK")
        self.pack_id = pack_id
        self.bank_mode = _bank_mode(self._root)

        corpus_file = packs / f"corpus_{pack_id}.txt"
        questions_file = packs / f"questions_{pack_id}.jsonl"
        manifest_file = packs / f"manifest_{pack_id}.json"
        if not corpus_file.is_file() or not questions_file.is_file():
            builder = _HERE / "packs" / "build_pack.py"
            remedy = (
                f"its manifest is there, so `python {builder} fetch {pack_id}` rebuilds "
                "it from the pinned dataset revision and verifies it"
                if manifest_file.is_file()
                else f"build one from a local shard with `python {builder} build ...` "
                "(bench/oolong/PROTOCOL.md)"
            )
            raise AssayError(
                f"OOLONG pack {pack_id!r} is not built in {packs} (need "
                f"corpus_{pack_id}.txt and questions_{pack_id}.jsonl): {remedy}"
            )

        self.corpus = corpus_file.read_text()
        self.corpus_sha256 = hashlib.sha256(self.corpus.encode("utf-8")).hexdigest()

        # Gold answers live here, PRIVATE, indexed by question id, never placed in
        # the corpus, the observation, or the journal. They are read only at
        # finalize() by the sealed scorer.
        self._questions: list[dict[str, Any]] = []
        self._gold_by_id: dict[int, dict[str, Any]] = {}
        for line in questions_file.read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            self._questions.append(record)
            self._gold_by_id[int(record["id"])] = record
        self._questions.sort(key=lambda item: int(item["id"]))
        if not self._questions:
            raise AssayError(f"OOLONG pack {pack_id!r} has no questions")

        # The agent's free offline read: the corpus, written into the run dir.
        self._corpus_path = (self._root / ".assay" / "corpus.txt").resolve()
        self._corpus_path.parent.mkdir(parents=True, exist_ok=True)
        self._corpus_path.write_text(self.corpus)

        try:
            self._manifest = json.loads(manifest_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self._manifest = {}

        # Mutable run state, rebuilt exactly on journal replay.
        self._banked: list[dict[str, Any]] = []
        self._submissions: list[dict[str, Any]] = []
        self._banks_for_current = 0
        self._refusals = 0
        self._covered: list[tuple[int, int]] = []
        self._last_result: dict[str, Any] = {
            "action": "START",
            "status": "ready",
            "detail": f"pack {pack_id} loaded; read the corpus at {self._corpus_path}",
        }
        self._closed = False
        self._cached_observation: dict[str, Any] | None = None

    # -- structure / census helpers ---------------------------------------

    def _structure_hint(self) -> dict[str, Any]:
        lines = self.corpus.splitlines()
        instance_lines = sum(1 for line in lines if " || User: " in line)
        return {
            "total_lines": len(lines),
            "instance_lines": instance_lines,
            "line_format": "Date: {Mon DD, YYYY} || User: {id} || Instance: {text}",
            "note": "header + one instance per line + footer; metadata is inline",
        }

    def _coverage_fraction(self) -> float:
        if not self.corpus:
            return 0.0
        covered = sum(end - start for start, end in _merge_ranges(self._covered))
        return round(covered / len(self.corpus), 6)

    def _find_span(self, span: str) -> int:
        """Verbatim substring offset, or -1. The referent-grounding check, in code."""
        return self.corpus.find(span)

    def _current_index(self) -> int:
        return len(self._submissions)

    def _current_question(self) -> dict[str, Any] | None:
        index = self._current_index()
        if index >= len(self._questions):
            return None
        question = self._questions[index]
        # Presented WITHOUT the gold answer.
        return {
            "index": index,
            "number": index + 1,
            "of": len(self._questions),
            "id": int(question["id"]),
            "task_group": question.get("task_group"),
            "answer_type": question.get("answer_type"),
            "text": question.get("question"),
        }

    def _census_ok_for_current(self) -> bool:
        return self._banks_for_current >= CENSUS_MIN_BANKS_PER_QUESTION

    # -- observation -------------------------------------------------------

    def _observe(self) -> dict[str, Any]:
        submitted = len(self._submissions)
        total = len(self._questions)
        done = submitted >= total
        state = "WIN" if done else "NOT_FINISHED"
        data = {
            "pack_id": self.pack_id,
            "corpus_path": str(self._corpus_path),
            "corpus_file": ".assay/corpus.txt",
            "corpus_bytes": len(self.corpus.encode("utf-8")),
            "corpus_chars": len(self.corpus),
            "corpus_sha256": self.corpus_sha256,
            "corpus_note": (
                "read this FILE with your own offline tools (free); the corpus is "
                "NOT in this observation by design"
            ),
            "structure_hint": self._structure_hint(),
            "question_count": total,
            "questions_submitted": submitted,
            "current_question": self._current_question(),
            "banked_count": len(self._banked),
            "banked": [
                {
                    "start": fact["start"],
                    "end": fact["end"],
                    "span_preview": fact["span"][:SPAN_PREVIEW],
                }
                for fact in self._banked[-8:]
            ],
            "refusals": self._refusals,
            "census": {
                "rule": CENSUS_RULE,
                "banks_for_current_question": self._banks_for_current,
                "min_banks_required": CENSUS_MIN_BANKS_PER_QUESTION,
                "current_ok": self._census_ok_for_current(),
                "coverage_fraction": self._coverage_fraction(),
                "covered_char_ranges": len(_merge_ranges(self._covered)),
                "planned": "unread-region ledger (per-question required-scope coverage)",
            },
            "last_result": self._last_result,
            "scoring": "SEALED: correctness computed only at finalize()",
        }
        if self.bank_mode == "batch":
            data["bank_mode"] = "batch"
        return {
            "data": data,
            "state": state,
            "levels_completed": submitted,
            "win_levels": total,
            "available_actions": [] if done else list(ACTIONS),
        }

    @property
    def observation(self) -> dict[str, Any]:
        if self._closed and self._cached_observation is not None:
            return self._cached_observation
        return self._observe()

    @property
    def public_info(self) -> dict[str, Any]:
        return {
            "game_id": self.pack_id,
            "title": f"OOLONG corpus pack {self.pack_id}",
            "benchmark": "oolong",
            "dataset_revision": self._manifest.get("dataset_revision"),
            "context_len": self._manifest.get("context_len"),
            "dataset": self._manifest.get("dataset"),
            "bank_mode": self.bank_mode,
        }

    # -- actions -----------------------------------------------------------

    def step(
        self,
        action: str,
        data: dict[str, Any] | None,
        reasoning: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if self._closed:
            raise AssayError("the OOLONG session is closed")
        name = str(action).upper()
        payload = dict(data or {})
        if name == "BANK_FACT":
            self._do_bank_fact(payload)
        elif name == "SUBMIT":
            self._do_submit(payload)
        else:
            raise AssayError(
                f"unknown action {action!r}; this world takes BANK_FACT and SUBMIT"
            )
        return self._observe()

    def _do_bank_fact(self, payload: Mapping[str, Any]) -> None:
        if self.bank_mode == "batch":
            self._bank_spans(payload)
            return
        text = _text(payload.get("text"), "text")
        span = _text(payload.get("span"), "span")
        offset = self._find_span(span)
        if offset < 0:
            # Refused, but journaled: the attempt is evidence (like an FLE
            # policy refusal). No state advances.
            self._refusals += 1
            self._last_result = {
                "action": "BANK_FACT",
                "status": "refused",
                "detail": "span is not a verbatim substring of the corpus",
                "span_preview": span[:SPAN_PREVIEW],
            }
            return
        end = offset + len(span)
        self._banked.append(
            {"text": text, "span": span, "start": offset, "end": end}
        )
        self._banks_for_current += 1
        self._covered.append((offset, end))
        self._last_result = {
            "action": "BANK_FACT",
            "status": "accepted",
            "detail": f"span verified at offset {offset}",
            "start": offset,
            "end": end,
            "span_preview": span[:SPAN_PREVIEW],
        }

    def _bank_spans(self, payload: Mapping[str, Any]) -> None:
        """Batch mode: several spans in one paid action. All verbatim, or the
        whole action is refused and journaled; each verified span is one
        banked fact for the current question."""
        spans = _spans(payload.get("spans"))
        text = _text(payload["text"], "text") if payload.get("text") else None
        offsets: list[tuple[int, int]] = []
        for span in spans:
            offset = self._find_span(span)
            if offset < 0:
                self._refusals += 1
                self._last_result = {
                    "action": "BANK_FACT",
                    "status": "refused",
                    "reason": "span_not_found",
                    "detail": (
                        "span is not a verbatim substring of the corpus; none of the "
                        f"{len(spans)} banked"
                    ),
                    "span_preview": span[:SPAN_PREVIEW],
                }
                return
            offsets.append((offset, offset + len(span)))
        for span, (start, end) in zip(spans, offsets):
            self._banked.append({"text": text or span, "span": span, "start": start, "end": end})
            self._covered.append((start, end))
        self._banks_for_current += len(spans)
        self._last_result = {
            "action": "BANK_FACT",
            "status": "accepted",
            "detail": f"{len(spans)} span(s) verified",
            "spans_verified": len(spans),
            "span_preview": spans[0][:SPAN_PREVIEW],
        }

    def _do_submit(self, payload: Mapping[str, Any]) -> None:
        answer = _text(payload.get("answer"), "answer")
        if self.bank_mode == "batch":
            spans = _spans(payload["spans"]) if payload.get("spans") else None
        else:
            spans = _spans(payload.get("spans"))
        current = self._current_question()
        if current is None:  # pragma: no cover - broker refuses acting past WIN
            raise AssayError("all questions already submitted")

        # Grounding gate: every cited span must be verbatim in the corpus.
        offsets: list[tuple[int, int]] = []
        for span in spans or ():
            offset = self._find_span(span)
            if offset < 0:
                self._refusals += 1
                self._last_result = {
                    "action": "SUBMIT",
                    "status": "refused",
                    "reason": "span_not_found",
                    "detail": f"cited span is not a verbatim substring: {span[:SPAN_PREVIEW]!r}",
                    "question_id": current["id"],
                }
                return
            offsets.append((offset, offset + len(span)))

        # Coverage census (v1).
        if not self._census_ok_for_current():
            self._refusals += 1
            self._last_result = {
                "action": "SUBMIT",
                "status": "refused",
                "reason": "census",
                "detail": (
                    f"coverage census: need >={CENSUS_MIN_BANKS_PER_QUESTION} verified "
                    f"BANK_FACT for this question, have {self._banks_for_current}"
                ),
                "question_id": current["id"],
            }
            return

        if spans is None:
            # Batch mode without a spans list: the citation is what was banked
            # for this question (the census just checked there is some).
            spans = [fact["span"] for fact in self._banked[len(self._banked) - self._banks_for_current:]]
        # Accepted: seal the answer (store; NEVER score or reveal mid-run).
        self._submissions.append(
            {
                "question_id": current["id"],
                "question_index": current["index"],
                "answer": answer,
                "spans": list(spans),
                "banks_used": self._banks_for_current,
            }
        )
        self._covered.extend(offsets)
        self._banks_for_current = 0
        self._last_result = {
            "action": "SUBMIT",
            "status": "sealed",
            "detail": (
                f"answer for question {current['number']}/{current['of']} sealed "
                "(scored at finalize)"
            ),
            "question_id": current["id"],
            "spans_verified": len(spans),
        }

    # -- sealed scoring at finalize ---------------------------------------

    def _load_scorer(self) -> Any:
        spec = importlib.util.spec_from_file_location(
            "oolong_scorer", _HERE / "scorer.py"
        )
        if spec is None or spec.loader is None:  # pragma: no cover
            raise AssayError("cannot load bench/oolong/scorer.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def finalize(self) -> None:
        """Score the sealed submissions with the reused OOLONG scorer and write a
        per-question + aggregate report to the run dir. Called by the broker on
        the WIN transition, the FIRST time the answer key is consulted."""
        if self._closed:
            return
        self._closed = True
        self._cached_observation = self._observe()

        scorer = self._load_scorer()
        report = scorer.score_submissions(
            self._submissions, self._gold_by_id, split="synth"
        )
        report["pack"] = {
            "pack_id": self.pack_id,
            "corpus_sha256": self.corpus_sha256,
            "corpus_chars": len(self.corpus),
            "dataset_revision": self._manifest.get("dataset_revision"),
            "context_len": self._manifest.get("context_len"),
            "dataset": self._manifest.get("dataset"),
        }
        report["evidence"] = {
            "banked_facts": len(self._banked),
            "refusals": self._refusals,
            "coverage_fraction": self._coverage_fraction(),
            "census_rule": CENSUS_RULE,
        }

        state_dir = self._root / ".assay"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "oolong_score.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n"
        )
        (state_dir / "oolong_score.md").write_text(_render_report_md(report))

    def close(self) -> None:  # symmetry; the broker uses finalize()
        self._closed = True


def _render_report_md(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    pack = report.get("pack", {})
    lines = [
        f"# OOLONG sealed score: pack {pack.get('pack_id', '?')}",
        "",
        f"- scorer: {report['scorer']['source']} "
        f"(`{report['scorer']['function']}`, sha256 "
        f"{report['scorer']['vendor_sha256'][:12]}…, dateutil={report['scorer']['dateutil']})",
        f"- answers fed as `{report['scorer']['answer_form']}`; network: "
        f"{report['scorer']['network']}",
        f"- dataset revision: {pack.get('dataset_revision')}, context_len "
        f"{pack.get('context_len')}, corpus sha256 "
        f"{str(pack.get('corpus_sha256'))[:12]}…",
        "",
        f"- **mean score {aggregate['mean_score']}** over {aggregate['scored']} "
        f"question(s); {aggregate['exact_hits']} exact hit(s); "
        f"sum {aggregate['sum_score']}",
        f"- by task group: {aggregate['by_task_group']}",
        f"- by answer type: {aggregate['by_answer_type']}",
        "",
        "| # | qid | group | type | submitted | parsed | gold | score |",
        "|---|-----|-------|------|-----------|--------|------|-------|",
    ]
    for index, question in enumerate(report["per_question"], 1):
        lines.append(
            f"| {index} | {question.get('question_id')} | "
            f"{question.get('task_group')} | {question.get('answer_type')} | "
            f"{str(question.get('submitted'))[:24]} | {str(question.get('parsed'))[:24]} | "
            f"{str(question.get('gold'))[:24]} | {question.get('score')} |"
        )
    evidence = report.get("evidence", {})
    lines.extend(
        [
            "",
            f"Evidence integrity: {evidence.get('banked_facts')} banked fact(s), "
            f"{evidence.get('refusals')} refused attempt(s), coverage "
            f"{evidence.get('coverage_fraction')}.",
            "",
        ]
    )
    return "\n".join(lines)


def factory(root: Path, config: Mapping[str, Any]) -> OolongSession:
    """Broker entry point: `--adapter .../bench/oolong/adapter.py:factory`."""
    return OolongSession(root, config)

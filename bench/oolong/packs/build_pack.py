"""Extract-and-dedup one OOLONG corpus pack from a dataset shard.

A pack is the adapter's whole storage model (RESEARCH.md §6.1, M0 §2): ONE
deduplicated corpus plus the question set that shares it. OOLONG stores the same
corpus verbatim on every one of the ~25 rows that share a `context_window_id`,
so a pack keeps a single copy and the questions beside it:

    corpus_{packid}.txt        the corpus string (one copy)
    questions_{packid}.jsonl   one question per line, gold answer kept ASIDE
    manifest_{packid}.json     provenance + pins (dataset revision, sha256, ...)

This is an OFFLINE, one-time tool. It reads a LOCAL parquet shard (no network)
and is run with an interpreter that has pyarrow (e.g. the M0 spike venv). The
adapter never imports this file or pyarrow — it reads the plain pack files.

The pack format does NOT depend on corpus length: the same three files describe
a 4K corpus and a 4M corpus. Long-corpus extraction (M2) only changes which
shard/row is read, never the pack shape.

Usage (spike venv):
    python build_pack.py --parquet <shard.parquet> --packid spam4k \\
        --dataset spam --context-len 4096 --out <packs-dir>
    python build_pack.py --parquet <shard.parquet> --packid spam4k \\
        --cwid 10006 --out <packs-dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import pyarrow.parquet as pq

# The pinned dataset revision this pack family is built from (HF
# `oolongbench/oolong-synth` refs/main at build time). Recorded for provenance
# and replay; see bench/oolong/PROTOCOL.md.
PINNED_REVISION = "f0d59eaf0febf130664cfceb710436c8e3216b2b"
DATASET_REPO = "oolongbench/oolong-synth"

_PACKID = re.compile(r"^[a-z0-9]{2,16}$")  # must be a valid ASSAY game id

# Fields carried into the pack's questions.jsonl. Everything the vendored OOLONG
# scorer reads (answer, answer_type, id, context_window_id, dataset) plus context
# for reports. The gold `answer` lives here, NEVER in the corpus or observation.
_QUESTION_FIELDS = (
    "id",
    "context_window_id",
    "dataset",
    "question",
    "task_group",
    "task",
    "answer",
    "answer_type",
    "context_len",
    "num_labels",
)


def _select_cwid(frame, dataset: str | None, context_len: int | None, cwid: int | None) -> int:
    if cwid is not None:
        if cwid not in set(frame["context_window_id"].tolist()):
            raise SystemExit(f"context_window_id {cwid} not in shard")
        return int(cwid)
    subset = frame
    if dataset is not None:
        subset = subset[subset["dataset"] == dataset]
    if context_len is not None:
        subset = subset[subset["context_len"] == context_len]
    if subset.empty:
        raise SystemExit(
            f"no rows for dataset={dataset} context_len={context_len} in shard"
        )
    # Deterministic: smallest context_window_id among the candidates.
    return int(sorted(subset["context_window_id"].unique())[0])


def build(
    parquet: Path,
    packid: str,
    out: Path,
    *,
    dataset: str | None,
    context_len: int | None,
    cwid: int | None,
) -> dict:
    if not _PACKID.fullmatch(packid):
        raise SystemExit(f"packid {packid!r} must match [a-z0-9]{{2,16}} (a valid game id)")
    frame = pq.ParquetFile(str(parquet)).read().to_pandas()
    chosen = _select_cwid(frame, dataset, context_len, cwid)
    group = frame[frame["context_window_id"] == chosen].sort_values("id")

    corpora = group["context_window_text"].unique().tolist()
    if len(corpora) != 1:
        raise SystemExit(
            f"context_window_id {chosen} has {len(corpora)} distinct corpora; "
            "expected exactly one (dedup invariant broken)"
        )
    corpus = corpora[0]

    out.mkdir(parents=True, exist_ok=True)
    corpus_path = out / f"corpus_{packid}.txt"
    questions_path = out / f"questions_{packid}.jsonl"
    manifest_path = out / f"manifest_{packid}.json"

    corpus_path.write_text(corpus)
    corpus_sha = hashlib.sha256(corpus.encode("utf-8")).hexdigest()

    rows = []
    with questions_path.open("w") as handle:
        for _, record in group.iterrows():
            row = {}
            for field in _QUESTION_FIELDS:
                value = record[field]
                # normalize numpy scalars to plain python
                if hasattr(value, "item"):
                    value = value.item()
                row[field] = value
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            rows.append(row)

    manifest = {
        "packid": packid,
        "benchmark": "oolong",
        "split": "synth",
        "dataset_repo": DATASET_REPO,
        "dataset_revision": PINNED_REVISION,
        "source_shard": Path(parquet).name,
        "context_window_id": chosen,
        "context_len": int(group.iloc[0]["context_len"]),
        "dataset": str(group.iloc[0]["dataset"]),
        "n_questions": len(rows),
        "task_groups": sorted({r["task_group"] for r in rows}),
        "answer_types": sorted({r["answer_type"] for r in rows}),
        "corpus_chars": len(corpus),
        "corpus_sha256": corpus_sha,
        "gold_location": "questions jsonl only (sealed; never in corpus/observation)",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", type=Path, required=True, help="local shard (no network)")
    parser.add_argument("--packid", required=True, help="pack id / ASSAY game id [a-z0-9]{2,16}")
    parser.add_argument("--out", type=Path, required=True, help="packs output directory")
    parser.add_argument("--cwid", type=int, help="explicit context_window_id to extract")
    parser.add_argument("--dataset", help="source dataset to filter by (e.g. spam)")
    parser.add_argument("--context-len", type=int, dest="context_len", help="context length to filter by")
    args = parser.parse_args()
    manifest = build(
        args.parquet,
        args.packid,
        args.out,
        dataset=args.dataset,
        context_len=args.context_len,
        cwid=args.cwid,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

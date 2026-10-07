"""Build, fetch and verify OOLONG corpus packs.

A pack is the adapter's whole storage model (RESEARCH.md section 6.1, M0
section 2): ONE deduplicated corpus plus the question set that shares it.
OOLONG stores the same corpus verbatim on every one of the ~25 rows that share
a `context_window_id`, so a pack keeps a single copy and the questions beside it:

    corpus_{packid}.txt        the corpus string (one copy)
    questions_{packid}.jsonl   one question per line, gold answer kept ASIDE
    manifest_{packid}.json     provenance and pins (dataset revision, shard,
                               context_window_id, sha256 of both files)

The repository ships the two smoke packs whole (spam4k, spam8k) and every other
pack as its manifest only: the dataset text stays out of the repository and is
rebuilt on first use from the pinned dataset revision, then checked against the
manifest's sha256 values. Three commands:

    python build_pack.py fetch synth128k
        Rebuild a pack whose manifest is present. The source shard is taken
        from, in order: --parquet, ASSAY's own cache ($XDG_CACHE_HOME/assay/
        oolong, default ~/.cache/assay/oolong), the Hugging Face hub cache if
        the pinned revision is there, else one download from the hub (HF_TOKEN
        is sent when set). The rebuilt files must hash to the manifest's
        values or they are deleted again.

    python build_pack.py verify synth128k
        Check a built pack against its manifest (stdlib only).

    python build_pack.py build --parquet <shard> --packid spam4k \\
        --dataset spam --context-len 4096
        Extract a new pack from a local shard and write its manifest.

Building and fetching need pyarrow and pandas (`pip install -e '.[oolong]'`).
The adapter never imports this file: it reads the plain pack files.

The pack format does NOT depend on corpus length: the same three files describe
a 4K corpus and a 4M corpus. Long-corpus extraction only changes which shard and
row is read, never the pack shape.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

# The pinned dataset revision this pack family is built from (HF
# `oolongbench/oolong-synth` refs/main at build time). Recorded in every
# manifest and used by `fetch`; see bench/oolong/PROTOCOL.md.
PINNED_REVISION = "f0d59eaf0febf130664cfceb710436c8e3216b2b"
DATASET_REPO = "oolongbench/oolong-synth"
DATASET_FILES = "data"  # the shards' directory inside the dataset repository
HUB = "https://huggingface.co"

HERE = Path(__file__).resolve().parent

# A pack id names files, so it is stricter than an ASSAY world id. Every pack
# id is also a valid world id.
_PACKID = re.compile(r"^[a-z0-9]{2,16}$")

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


# --- paths and hashes ---------------------------------------------------------


def pack_files(out: Path, packid: str) -> tuple[Path, Path, Path]:
    """(corpus, questions, manifest) paths of a pack in `out`."""
    return (
        out / f"corpus_{packid}.txt",
        out / f"questions_{packid}.jsonl",
        out / f"manifest_{packid}.json",
    )


def check_packid(packid: str) -> str:
    if not _PACKID.fullmatch(packid):
        raise SystemExit(f"pack id {packid!r} must match [a-z0-9]{{2,16}}")
    return packid


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def questions_text(rows: list[dict]) -> str:
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


def read_manifest(out: Path, packid: str) -> dict:
    manifest_path = pack_files(out, packid)[2]
    if not manifest_path.is_file():
        raise SystemExit(
            f"no manifest for pack {packid!r} at {manifest_path}: a pack without a "
            "manifest is built from a local shard with `build`, not fetched"
        )
    return json.loads(manifest_path.read_text())


def default_cache() -> Path:
    base = os.getenv("XDG_CACHE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "assay" / "oolong"


# --- verify -------------------------------------------------------------------


def verify(out: Path, packid: str) -> list[str]:
    """Problems with a built pack against its manifest; an empty list is a pass."""
    corpus_path, questions_path, _ = pack_files(out, packid)
    manifest = read_manifest(out, packid)
    problems: list[str] = []
    for label, path in (("corpus", corpus_path), ("questions", questions_path)):
        if not path.is_file():
            problems.append(f"{label}: {path.name} is missing")
            continue
        expected = manifest.get(f"{label}_sha256")
        if not expected:
            problems.append(f"{label}: the manifest has no {label}_sha256 to check against")
            continue
        actual = sha256_file(path)
        if actual != expected:
            problems.append(
                f"{label}: sha256 {actual[:12]} differs from the manifest's {expected[:12]}"
            )
    if questions_path.is_file():
        count = sum(1 for line in questions_path.read_text().splitlines() if line.strip())
        if count != manifest.get("n_questions"):
            problems.append(
                f"questions: {count} lines, the manifest says {manifest.get('n_questions')}"
            )
    return problems


# --- build from a shard -------------------------------------------------------


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


def extract(
    parquet: Path,
    packid: str,
    *,
    dataset: str | None = None,
    context_len: int | None = None,
    cwid: int | None = None,
) -> tuple[str, list[dict], dict]:
    """(corpus, question rows, manifest) for one context window of a shard."""
    try:
        import pyarrow.parquet as pq
    except ImportError:  # pragma: no cover - environment dependent
        raise SystemExit(
            "building a pack needs pyarrow and pandas: pip install -e '.[oolong]'"
        ) from None
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

    rows = []
    for _, record in group.iterrows():
        row = {}
        for field in _QUESTION_FIELDS:
            value = record[field]
            if hasattr(value, "item"):  # numpy scalars to plain python
                value = value.item()
            row[field] = value
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
        "corpus_sha256": sha256_text(corpus),
        "questions_sha256": sha256_text(questions_text(rows)),
        "gold_location": "questions jsonl only (sealed; never in corpus/observation)",
    }
    return corpus, rows, manifest


def write_pack(
    out: Path, packid: str, corpus: str, rows: list[dict], manifest: dict | None
) -> None:
    """Write the corpus and questions, and the manifest when one is given."""
    out.mkdir(parents=True, exist_ok=True)
    corpus_path, questions_path, manifest_path = pack_files(out, packid)
    corpus_path.write_text(corpus)
    questions_path.write_text(questions_text(rows))
    if manifest is not None:
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def build(
    parquet: Path,
    packid: str,
    out: Path,
    *,
    dataset: str | None,
    context_len: int | None,
    cwid: int | None,
) -> dict:
    check_packid(packid)
    corpus, rows, manifest = extract(
        parquet, packid, dataset=dataset, context_len=context_len, cwid=cwid
    )
    write_pack(out, packid, corpus, rows, manifest)
    return manifest


# --- fetch --------------------------------------------------------------------


def _hub_cache_shard(manifest: dict) -> Path | None:
    """The shard inside a Hugging Face hub cache, when the pinned revision is there."""
    if os.getenv("HF_HUB_CACHE"):
        root = Path(os.environ["HF_HUB_CACHE"]).expanduser()
    elif os.getenv("HF_HOME"):
        root = Path(os.environ["HF_HOME"]).expanduser() / "hub"
    else:
        root = Path.home() / ".cache" / "huggingface" / "hub"
    repo_dir = "datasets--" + str(manifest["dataset_repo"]).replace("/", "--")
    candidate = (
        root / repo_dir / "snapshots" / str(manifest["dataset_revision"])
        / DATASET_FILES / str(manifest["source_shard"])
    )
    return candidate if candidate.is_file() else None


def _download(url: str, target: Path) -> None:
    headers = {"User-Agent": "assay-oolong-packs"}
    token = os.getenv("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=target.parent, delete=False)
    try:
        with urllib.request.urlopen(request, timeout=60) as response, handle:
            size = response.headers.get("Content-Length")
            print(f"fetching {url}" + (f" ({int(size) / 1e6:.1f} MB)" if size else ""))
            shutil.copyfileobj(response, handle, length=1 << 20)
        Path(handle.name).replace(target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def locate_shard(manifest: dict, cache: Path, parquet: Path | None) -> Path:
    """The manifest's source shard, from the first place that has it."""
    if parquet is not None:
        if not parquet.is_file():
            raise SystemExit(f"no such shard: {parquet}")
        return parquet
    shard = str(manifest["source_shard"])
    revision = str(manifest["dataset_revision"])
    cached = cache / revision / shard
    if cached.is_file():
        return cached
    from_hub_cache = _hub_cache_shard(manifest)
    if from_hub_cache is not None:
        return from_hub_cache
    url = f"{HUB}/datasets/{manifest['dataset_repo']}/resolve/{revision}/{DATASET_FILES}/{shard}"
    _download(url, cached)
    return cached


def fetch(out: Path, packid: str, cache: Path, parquet: Path | None) -> dict:
    """Rebuild a pack from its manifest's pinned shard and verify it. The
    manifest in `out` is the authority and is never rewritten."""
    check_packid(packid)
    manifest = read_manifest(out, packid)
    shard = locate_shard(manifest, cache, parquet)
    corpus, rows, built = extract(shard, packid, cwid=int(manifest["context_window_id"]))
    write_pack(out, packid, corpus, rows, manifest=None)
    problems = verify(out, packid)
    if problems:
        corpus_path, questions_path, _ = pack_files(out, packid)
        corpus_path.unlink(missing_ok=True)
        questions_path.unlink(missing_ok=True)
        raise SystemExit(
            f"pack {packid!r} rebuilt from {shard} does not match its manifest, "
            "the files were removed again:\n  " + "\n  ".join(problems)
        )
    return {"packid": packid, "shard": str(shard), "n_questions": built["n_questions"]}


# --- command line -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    fetch_parser = commands.add_parser(
        "fetch", help="rebuild a pack from its manifest's pinned shard"
    )
    fetch_parser.add_argument("packid")
    fetch_parser.add_argument(
        "--out", type=Path, default=HERE, help="packs directory (default: this one)"
    )
    fetch_parser.add_argument(
        "--cache", type=Path, default=None,
        help="shard cache (default: $XDG_CACHE_HOME/assay/oolong)",
    )
    fetch_parser.add_argument(
        "--parquet", type=Path, default=None,
        help="use this local shard instead of any cache or download",
    )

    verify_parser = commands.add_parser("verify", help="check a built pack against its manifest")
    verify_parser.add_argument("packid")
    verify_parser.add_argument("--out", type=Path, default=HERE)

    build_parser = commands.add_parser("build", help="extract a new pack from a local shard")
    build_parser.add_argument("--parquet", type=Path, required=True, help="local shard")
    build_parser.add_argument("--packid", required=True, help="pack id, [a-z0-9]{2,16}")
    build_parser.add_argument("--out", type=Path, default=HERE)
    build_parser.add_argument("--cwid", type=int, help="explicit context_window_id to extract")
    build_parser.add_argument("--dataset", help="source dataset to filter by (e.g. spam)")
    build_parser.add_argument(
        "--context-len", type=int, dest="context_len", help="context length to filter by"
    )

    args = parser.parse_args(argv)
    if args.command == "fetch":
        result = fetch(args.out, args.packid, args.cache or default_cache(), args.parquet)
        print(
            f"pack {result['packid']} built from {result['shard']}: "
            f"{result['n_questions']} questions, verified against its manifest"
        )
        return 0
    if args.command == "verify":
        problems = verify(args.out, args.packid)
        if problems:
            print(f"pack {args.packid}: FAIL\n  " + "\n  ".join(problems))
            return 1
        print(f"pack {args.packid}: ok")
        return 0
    manifest = build(
        args.parquet, args.packid, args.out,
        dataset=args.dataset, context_len=args.context_len, cwid=args.cwid,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

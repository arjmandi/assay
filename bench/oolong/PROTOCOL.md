# OOLONG corpus-pack protocol (M1)

The query-framed OOLONG adapter for ASSAY (RESEARCH.md §6.1, §7). ASSAY refuses
to play the long-context game: the corpus is a **document observer** written to a
file in the run directory; the agent reads it with its own offline tools **for
free**, and the model's window never holds the corpus. The referee grades
**evidence integrity** — a static corpus has no world-response against which to
grade a prediction, so a fact may only be *banked*, and an answer only
*submitted*, carrying a span-verifiable citation checked verbatim **in code**.
Correctness is scored **sealed**, only at `finalize()`.

Everything here is bench-layer: an adapter, two registry rows, a vendored scorer,
and pack files. The kernel is untouched (`git diff --stat main -- src/` is empty).

## Run

    assay start spam4k \
        --adapter <repo>/bench/oolong/adapter.py:factory \
        --registry <repo>/bench/oolong/registry_40.json

- `registry_40.json` — budget 40 actions, for small packs (≈≤5 questions).
- `registry_200.json` — budget 200 actions, for ≈25-question packs (the 8K
  corpora). Size the cap ≈4–8× the question count (RESEARCH.md §7).

Which pack loads: the ASSAY game id (`config["game_id"]`), overridable with
`ASSAY_OOLONG_PACK`. The packs directory is `ASSAY_OOLONG_PACKS` or the adapter's
own `packs/`. One run directory = one corpus + its question set.

## Pack format

A pack is the whole storage model (M0 §2). OOLONG repeats the same corpus
verbatim on all ~25 rows sharing a `context_window_id`, so a pack keeps **one**
copy:

    packs/corpus_{id}.txt        the corpus string (one deduplicated copy)
    packs/questions_{id}.jsonl   one question per line; gold answer kept ASIDE
    packs/manifest_{id}.json     provenance + pins (see below)

`{id}` is the pack id, `[a-z0-9]{2,16}`: it names files and is also the run's
world id.
`questions_{id}.jsonl` carries every field the scorer reads (`id`,
`context_window_id`, `dataset`, `answer`, `answer_type`) plus `question`,
`task_group`, `task`, `context_len`, `num_labels`. The gold `answer` lives here
only — **never** in the corpus, the observation, or the journal. The manifest
pins the dataset revision, the source shard, the `context_window_id` and the
sha256 of both files, which is what `fetch` and `verify` check.

The format does **not** depend on corpus length: the same three files describe a
4K corpus and a 4M corpus. Long-corpus extraction (M2) changes only which
shard/row is read, never the pack shape or the adapter.

The two smoke packs ship whole: `spam4k` (5 questions, 4096-token corpus,
10 115 chars) and `spam8k` (25 questions, 8192-token corpus, 19 709 chars). The
length-ladder packs ship as manifests and are rebuilt on first use (README.md,
Packs). The builder needs pyarrow and pandas (`pip install -e '.[oolong]'`);
the adapter itself never imports either:

    python bench/oolong/packs/build_pack.py fetch synth128k      # rebuild from the pinned shard, verify
    python bench/oolong/packs/build_pack.py verify synth128k     # check a built pack (stdlib)
    python bench/oolong/packs/build_pack.py build \
        --parquet <local-shard.parquet> --packid spam4k \
        --dataset spam --context-len 4096                         # a new pack from a local shard

## Observation

A general (non-grid) observation. `data` carries the corpus **path**, size,
sha256, and a structure hint; the CURRENT question (text, group, answer_type —
**no gold**); banked-fact and census summaries; and `last_result`. It never
carries the corpus body. `win_levels` = number of questions; `levels_completed` =
questions accepted through the gate; state `WIN` when all are submitted with the
census clean. `available_actions` = `BANK_FACT`, `SUBMIT`.

## Actuators (both paid, both grounded in code)

Arguments are base64 because ASSAY action tokens are whitespace-split (same
convention as `bench/factorio` RUN).

- **`BANK_FACT text=<b64> span=<b64>`** — commit a fact citing a corpus span.
  The span is verified as a verbatim substring (`str.find`) in code. A span not
  present is **refused** and the attempt is **journaled** (evidence, like an FLE
  policy refusal); no state advances.
- **`SUBMIT answer=<b64> spans=<b64 json-list>`** — answer the current question,
  citing verbatim spans (each `str.find`-checked). Also coverage-gated (census
  below). On acceptance the answer is recorded **sealed** and the question
  pointer advances. `spans` is base64 of a JSON array of non-empty strings.

Because a registry run is daemon-gated, paid actions go through `assay act`
(carrying a `--predict`); a bare step is refused. Predictions grade against the
dict observation (`change`, `level+1`, host channel `level`, or a declared
channel such as `assay channel declare banked --path banked_count`).

## Census (the coverage gate)

**v1 (implemented, deliberately minimal):** SUBMIT is refused unless the agent
has banked **≥1 verified fact for the current question** (the counter resets
after each accepted SUBMIT), and every SUBMIT span is verbatim in the corpus.
This is honest-but-minimal: it enforces "consult and cite the corpus for each
answer" without a sophisticated census blocking M1. The observation also exposes
`coverage_fraction` (union of verified span ranges ÷ corpus length) and
`covered_char_ranges` as scaffolding — **reported, not gated.**

**Planned (M2+):** the full **unread-region ledger** (RESEARCH.md §6.1): gate on
per-question *required-scope* coverage — no open sub-question with an unconsulted
matching region — rather than a flat fact count.

## Sealed scoring at finalize

The broker calls `session.finalize()` on the WIN transition — the first and only
time the answer key is consulted. `finalize()` scores every sealed SUBMIT with
the **reused OOLONG scorer** (`scorer.py` → `vendor/oolong_eval_helpers.py`,
byte-identical to upstream; see `vendor/PROVENANCE.md`): exact match for
labels/comparisons/users, `0.75**|gold−pred|` partial credit for numbers,
`strptime` gold + parsed output for dates (RESEARCH.md §1.3). Answers are fed in
OOLONG's canonical `"Answer: {x}"` form for parser parity (M0 §3 caveat). The
report lands at `.assay/oolong_score.json` and `.assay/oolong_score.md`
(per-question + aggregate, plus evidence-integrity counts).

The scorer's heavy/paid deps (`litellm`, `datasets`, `tiktoken`, `transformers`,
`jsonlines`) are stubbed and `dateutil` is shimmed when absent, so scoring runs
with **zero network, zero API keys, no model** — the isolation M0 §3 proved. If
python-dateutil is not importable in the daemon runtime, DATE *model-output*
parsing uses a small format shim (gold parsing is stdlib and unaffected); install
python-dateutil for full DATE parity. NUMERIC/LABEL/COMPARISON/USER are
unaffected.

## The E5 variant: batch banking

`registry_200_batch.json` sets `control.bank_mode: batch`. The registry's
`control` block is declared-only for the kernel (journaled, pinned and
hashed, no kernel behavior) and is read by the adapter from the run's pinned
copy. Everything above holds except the shape of the two actuators:

- **`BANK_FACT spans=<b64 json-list>`** banks several facts in one paid
  action. Every span is verified as a verbatim substring in code; one missing
  span refuses the whole action (journaled, nothing banked). Each verified
  span is one banked fact for the current question.
- **`SUBMIT answer=<plain text>`** answers in plain text. Action tokens are
  whitespace-split, so a space is written as `_` (`more_common_than`,
  `February_2022`). The census is unchanged (at least one verified span
  banked for the current question), and those banked spans are the citation
  recorded in the sealed submission. A registry that also gives SUBMIT a
  `spans` parameter has it checked verbatim as in single mode.

The default, `single`, is what every published OOLONG run was recorded under
and what `registry_40.json` and `registry_200.json` select. Scoring is the
same code in both modes. The test suite drives the spam4k pack to WIN in both
(`tests/test_oolong_tenant.py`).

## Version pins & determinism

- Dataset: `oolongbench/oolong-synth`, split `validation`, revision
  **`f0d59eaf0febf130664cfceb710436c8e3216b2b`** (recorded in every manifest).
- Scorer: `vendor/oolong_eval_helpers.py`, upstream commit
  `0bb7eabe839218fee7fe8d007f41cfc2fd3ae24c`, sha256
  `247583a3b653b91c39fb88a102460802f1493175b23d05a7217aead0d685c64c` (MIT).
- Corpus load and every span check are pure `str.find` — **no network, time, or
  randomness at run time**. A static corpus trivially satisfies journal-replay
  resume (RESEARCH.md §7): killing the broker mid-run and resuming replays the
  journal to identical observations (verified — see RESULTS.md, acceptance F).

## Files

    adapter.py            factory + OolongSession (the world adapter)
    scorer.py             sealed-scoring wrapper over the vendored OOLONG scorer
    registry_40.json      BANK_FACT + SUBMIT rows, 40-action budget (small packs)
    registry_200.json     same rows, 200-action budget (~25-question packs)
    registry_200_batch.json   the E5 variant: control.bank_mode batch, BANK_FACT {spans}, SUBMIT {answer}
    packs/build_pack.py   fetch, verify and build packs (fetch and build need pyarrow and pandas)
    packs/{corpus,questions,manifest}_{id}.*   the smoke packs whole, the others as manifests
    vendor/oolong_eval_helpers.py   upstream OOLONG scorer, verbatim (MIT)
    vendor/{LICENSE.oolong,PROVENANCE.md}


## M2 length-ladder sweep — PRE-REGISTERED 2026-08-26 (before any scored run)

Decision 19: single-arm length-invariance, no three-arm E-C7. Contrast is
OOLONG's own PUBLISHED bare-model degradation (cited, not re-run). 128K is the
anchor; the flatness 128K -> 4M is the demonstration.

Rungs (oolongbench/oolong-synth, test split, pinned revision
`f0d59eaf0febf130664cfceb710436c8e3216b2b`):

| rung | context_len | cwid | questions | groups (count/user/time) |
|---|---|---|---|---|
| synth128k | 131072 | 40021 | 25 | 3/11/11 |
| synth1m | 1048576 | 90032 | 25 | 5/7/13 |
| synth4m | 4194304 | 80021 | 20 | 5/9/6 |

Protocol: Opus 5; registry_200.json (budget 200 actions per rung); one run
directory per rung; the agent reads the corpus FILE offline (free), pays only
BANK_FACT (span verbatim-checked) and SUBMIT (span + v1 census gated); answers
sealed, scored only at finalize by the vendored OOLONG scorer. Run sequentially,
cheapest rung first. Budget cap: $100 hard for the sweep.

Pre-registered readings (stated before results): accuracy and $/question stay
approximately FLAT across 128K -> 1M -> 4M (length-invariance), while OOLONG's
published bare-model accuracy falls with length and cannot run at 4M at all.
A large accuracy drop at the long rungs would weaken the invariance claim and
we would report it. Citation-integrity (all SUBMIT spans verbatim) and coverage
are captured as byproducts.

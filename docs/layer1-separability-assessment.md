# Layer 1 separability assessment

For arjmandi/me#741 (`Q-D1`). Question asked: can the "verification surface"
(journal format spec, standalone audit/replay verifier, claim-grammar spec) be
opened without touching the kernel (layer 3) or changing benchmark behaviour?
This is a read-only assessment — no refactor was started, no files were moved.

One nit on the premise: `docs/STRATEGY-2026-08-23.md`, referenced in the issue
as already committed here, does not exist in this repo (`git log --all` finds
no trace). The four-layer definitions quoted in the issue body were sufficient
to do this assessment; flagging the gap in case that doc was meant to land
here separately.

**Recommendation: low risk.** The functions layer 1 would publish already run
as a self-contained cluster with no import path into the kernel or the
capability layer — that direction already goes the other way. The two real
risks are not architectural, they're process risks (naming below), and both
are avoidable with no code change.

## 1. Is layer 1 actually separable today?

Grepped every internal import in `src/assay/` (`grep -rn "from \."`). The
package splits cleanly into four groups by *what imports what*, and the split
matches the issue's four layers almost exactly:

**The verification cluster** (candidate layer 1) — `core.py`, `textobs.py`,
`evidence.py`, `integrity.py`, `predictions.py`, `channels.py`, `verifiers.py`.
2,085 lines total. Internal imports inside this cluster: `integrity`→`core`,
`predictions`→`core`+`textobs` (deferred: `channels`, `verifiers`),
`channels`→`core` (deferred: `verifiers`), `verifiers`→`core`,
`evidence`→`core`+`textobs`. None of the seven import `broker`, `live`,
`agenda`, `modules`, `model`, `rules`, or `registry` — with one narrow
exception in `core.py` noted below.

**The kernel** (layer 3) — `broker.py` (the run loop / daemon) and `live.py`
(per-action gate + execution). They're mutually recursive: `live.py` imports
`broker_step` from `broker.py` at module level, `broker.py` imports back from
`.live` deferred inside a function (`broker.py:469`) to break the cycle.
Crucially, `live.py` calls straight into the verification cluster —
`extend_chain`, `redact`/`redact_mapping`, `admit_verifier`,
`grade_action_claims` are called inline during the live per-action path
(`live.py:134,234,305,443` etc.). This is the load-bearing fact for the
behaviour question below.

**Capability sauce** (layer 4) — `model.py` (the batching-law / world-model
tier, docstring literally says `PROMOTION LAW (pinned)`), `rules.py` (the grid
rules tier), `modules.py` (behaviour modules — each one *carries a `DOCTRINE`
string field*, confirmed by grep), `agenda.py` (goal/proposal lane, owner
authority, emergence meter). All four import *from* the verification cluster
(`model.py` deferred-imports `verifiers`, `aggregates`, `integrity`,
`predictions`; `carryover.py` deferred-imports `model`, `verifiers`) — never
the reverse. Dependency direction is one-way: capability sauce sits on top of
the verification primitives, it does not reach down to redefine them.

**Benchmark parity kit** (layer 2) — `registry.py`. Imports only `core.py`.

**The one coupling worth naming exactly**: `core.py:393` has a deferred
`from .registry import parse_registry_action`, triggered only when
`parse_action()` is called with a registry object — i.e. only when the *CLI*
parses a fresh action token in registry mode. A replay/audit tool never
constructs new action tokens — it reads already-materialized event dicts from
`events.jsonl` — so this path never fires for `assay-verify`. It's a
real edge in the import graph, but not one a verifier needs to cross.

**Conclusion**: layer 1 is not something that needs inventing — it already
exists as a distinguishable, one-directional dependency island. The couplings
that exist all point from kernel/capability *down into* it, never up.

## 2. Would `assay-verify` need layer 3/4 imports?

No, based on the current import graph. A standalone verifier reading a
finished run's `.assay/` directory needs exactly:

- `core.py` — but only its read side: `RunPaths`, `load_jsonl`, `load_events`,
  `frame_at`, `rows_to_grid`, `normalize_state`. The write side
  (`append_event`, `make_event`, `atomic_json`, `run_lock`, `command_status`)
  is dead weight for a verifier, not a hazard — nothing there depends on
  kernel state.
- `textobs.py` — general-observation diffing, zero internal imports.
- `integrity.py` — `compute_chain`, `audit`, `ungated_events`: the chain
  recompute and the ungated-event scan. Already 100% self-contained; `assay
  audit` (`cli.py:775-776`) is a two-line wrapper around exactly this today.
- `predictions.py` / `channels.py` / `verifiers.py` — re-deriving a claim's
  `ok`/`actual` from the journaled before/after observations: grid claims,
  general claims, channel claims, and re-executing a stored `verify:` file in
  the same sandboxed subprocess the kernel used at grade time.

None of that touches `broker.py`, `live.py`, `agenda.py`, `modules.py`,
`model.py`, `rules.py`, or `registry.py`.

One packaging fact, not a coupling risk: to fully re-run `verify:`/channel-
extractor claims, a third party needs more than `events.jsonl` — they need
`.assay/verifiers/*.py`, `.assay/channels/*.py` (the content-hashed stored
copies), and `chain.json`/the anchor file. That's a "what to bundle" question
for whoever writes the format spec, not an import-graph problem.

## 3. Does the split risk changing benchmark behaviour?

**No behavioural change to the run loop — same code path during runs, not a
refactor that touches it.** The evidence is direct, not inferred: `live.py`
already calls `grade_action_claims`, `extend_chain`, `redact_mapping`, and
`admit_verifier` as ordinary library calls from within the daemon's per-action
path. A "split" that moves the verification cluster's seven files into a
separately versioned/importable unit and repoints `live.py`'s/`broker.py`'s/
`cli.py`'s imports at the new location changes *where the code lives*, not
*what runs* — same functions, same call sites, same arguments. The 2,085
lines don't need to change to be extracted; only the import statements in the
handful of files that call into them do (`live.py`, `cli.py`, and the deferred
imports inside `model.py`/`carryover.py`/`aggregates.py`/`inspect.py`).

How this would actually be verified before merging the real split (not done
in this run, this is the acceptance gate to write into that PR): run the
`examples/counter_world.py` demo end-to-end pre- and post-refactor with a
fixed seed and diff `events.jsonl` byte-for-byte, plus the full existing suite
(`tests/test_v1rc1_e2e.py`, `test_e2e_adapter.py` spawn the real CLI + broker
subprocess — that's already an end-to-end behavioural check, not a mock). If
both are identical, the refactor is provably a no-op on behaviour.

**The actual medium-term risk is not this split, it's fork drift afterward.**
If the published layer 1 becomes a *copy* that the private kernel keeps
diverging from privately, "verify independently" quietly stops being true
over time — the public spec would describe code the private kernel no longer
runs. The fix costs nothing extra: keep one source of truth and have the
private kernel *import* the published package as a versioned dependency. That
matches the dependency direction already observed above (kernel depends on
the verification cluster, not vice versa), so it's not a new constraint, it's
formalizing the one that already exists.

## 4. How much work, and what would an independent party get?

Rough, not a commitment:

- Writing the three specs (journal format / event schema, claim grammar,
  audit+replay algorithm) fresh from source — see the drafting note in §5 on
  why "fresh" matters here: ~1–2 days.
- Repackaging the verification cluster (7 files, 2,085 lines) as a standalone
  importable unit, splitting `core.py`'s read/write halves if that's wanted,
  plus the golden-journal regression test from §3: ~2–4 days.
- New glue code: there is no existing "replay this whole journal and diff
  recorded vs. recomputed grades" entry point today — `grade_action_claims`
  is called once per live action, never in a batch-replay loop. Building that
  loop + a minimal CLI wrapper (`assay-verify path/to/run`): ~1–2 days
  including tests.
- Total: roughly one to one-and-a-half person-weeks, not a large project.

Small aside, not blocking: `channels.py` and `verifiers.py` each define their
own near-identical sandboxed-subprocess `_RUNNER` string and rlimit setup.
Worth deduplicating *before* publishing so the public surface has one
sandboxing implementation instead of two copies to keep in sync — a cleanup,
not a risk.

With layer 1 in hand, an independent party could fully confirm: journal
contiguity, hash-chain and anchor integrity, the ungated-event definition
(and that it correctly invalidates a run), and — the big one — that every
claim's recorded `ok`/`actual` is what the stated grammar and the stored
verifier code actually produce from the recorded before/after observations.
That is the complete "is this score real" check. What it can *not* confirm:
that the adapter/world (layer 2, per-bench) behaved fairly, or anything about
how well an agent played (that's strategy, layer 4) — verification and
capability are different questions by design, which is exactly why the split
is coherent.

## 5. Does DOCTRINE.md leak through a layer-1 artifact?

Checked: `DOCTRINE.md`'s "claim grammar" and "verifier contract" sections
(lines 62–133) are close prose restatements of `predictions.py`'s
`CLAIMS_HELP`/`GENERAL_CLAIMS_HELP` strings and `verifiers.py`'s module
docstring. That overlap is unavoidable and not a leak — both documents are
describing the same wire format, and a claim-grammar spec has to say what a
`verify:` claim or a `ch NAME delta = V` claim means or it isn't a spec.
Checked the artifacts that would actually ship with layer 1 —
`examples/counter_world.py`, `examples/example_registry.json`, the two claims
help strings — none contain DOCTRINE-specific language; they're a generic
toy world and mechanical parser/CLI text (confirmed: `grep -rn DOCTRINE
src/ examples/ tests/` only hits `modules.py`, where each behaviour module
literally carries a `DOCTRINE: str` field — that's a different, unrelated use
of the word).

The actual capability sauce — the batching law's promotion criteria
(`model.py`), the built-in behaviour modules' doctrine paragraphs
(`modules.py`), the goal-proposal/emergence machinery (`agenda.py`), and the
*strategic* advice in `DOCTRINE.md` ("vagueness earns nothing," "prefer a
verifier: it is the sharpest claim available," the notes discipline, the
gates-you-may-hit playbook) — lives entirely outside the verification
cluster's import graph and is authored only in `DOCTRINE.md`'s prose. There's
no code path pulling any of it into what layer 1 would publish.

**The one real risk is authorial, not architectural**: `DOCTRINE.md`
interleaves grammar and strategy in the same sections (the claim-grammar
table sits right next to "Free text that is not a claim is kept as
commentary... vagueness earns nothing"). Whoever drafts the actual
claim-grammar spec needs to write it from the source docstrings
(`CLAIMS_HELP`/`GENERAL_CLAIMS_HELP` in `predictions.py`, the module
docstrings in `integrity.py`/`verifiers.py`/`channels.py`), not by
copy-pasting `DOCTRINE.md` sections — the mechanical parts are separable by
hand, but only if someone does that separation deliberately instead of
reusing the already-well-written prose that happens to sit in the closed
file.

## Bottom line

Low risk, conditional on two things that cost nothing extra to do right:

1. The kernel imports the published layer 1 as its single source of truth
   (no private fork) — otherwise "verify independently" degrades silently
   over time as the two copies drift.
2. Whoever writes the claim-grammar spec drafts it from source docstrings,
   not from `DOCTRINE.md` prose — the content doesn't overlap by accident,
   but the *document* does, and that's a five-minute drafting instruction to
   give, not a structural obstacle.

No refactor was performed in this run.

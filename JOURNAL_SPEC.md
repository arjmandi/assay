# ASSAY journal format — spec v1

Status: **v1**, frozen. This document is precise enough that a third party can
write their own reader/verifier without ASSAY's source — that is the point of
publishing it. The companion document is `CLAIM_GRAMMAR.md` (the prediction
string grammar and the shape of a graded claim, referenced below from the
`grade` field). A reference, stdlib-only implementation ships in `verify/`.

This spec describes the artifacts a run directory contains and how to
recompute an integrity verdict from them. It does not describe ASSAY's
runtime behavior (the gate, the broker, modules, memory/agency) — those are
closed; the journal they *produce* is what this spec covers.

## 1. Run directory layout

A run directory (created by `assay start`) contains:

```
<run>/
  .assay/
    config.json       run identity: game_id, seed, mode, adapter, created_at,
                       binding_hash, registry_hash (if a registry was used)
    registry.json      the registry spec, verbatim, if the run used one
    events.jsonl        THE JOURNAL — append-only, one JSON object per line
    chain.json          {"event_id": <int>, "head": "<sha256 hex>"} — the
                        daemon's own record of the chain head after the last
                        append; §3 shows how to recompute it independently
    audit.json          the last verdict `assay audit` computed and wrote;
                        informational only — never trust it over a fresh
                        recompute, since it lives inside the same directory
                        an attacker controls
    activity.jsonl       command telemetry (pid, timing, status); not part
                        of the integrity verdict
    mutations.jsonl      the broker's own append-before-return log, used to
                        recover an event the CLI died before journaling;
                        not part of the integrity verdict
    verifiers/stats.json  per-verifier-program grading counters (vacuousness
                        detection); not part of the integrity verdict
```

External to the run directory: **anchors**. Periodically (every 25 events)
and on WIN, the daemon appends `{"event_id", "head", "run"}` to a JSONL file
*outside* the run directory — by default
`~/.assay/anchors/<sha256(run_dir_abspath)[:24]>.jsonl`, overridable with
`ASSAY_ANCHOR_DIR`. The point of anchoring outside the run directory is that
an attacker who controls the run directory's contents cannot also rewrite
history that was anchored elsewhere before the tamper. **When sharing a
verdict with a third party, publish the bare `{event_id, head}` pairs, not
the anchor file itself** — the file also carries the run's absolute local
path, which is operator machine detail, not part of the trust claim.

## 2. `events.jsonl` — the journal

One JSON object per line, UTF-8, `\n`-terminated, appended in order and never
rewritten in place. Every event carries at least:

| field | type | meaning |
|---|---|---|
| `id` | int | 0-based, must equal the line's position in the file (§4) |
| `timestamp` | str | ISO-8601 UTC, set at append time |
| `action` | str | the actuator name, or `START` (event 0) / `RESET` |
| `data` | object or null | the typed, registry-validated parameters |
| `note` | str | free text (`--because`), redacted (§6) |
| `state` | str | `NOT_FINISHED` \| `WIN` \| `GAME_OVER` |
| `levels_completed` | int | milestones completed so far |
| `level_before` | int or null | `levels_completed` before this event (null at event 0) |
| `win_levels` | int | total milestones the world declares |
| `available_actions` | array | usable actions after this event (ints for grid/numbered-action runs, strings for general/registry runs) |
| `counts_action` | bool | true for every event except `START` — the "was anything spent" flag §5 keys off |

Two mutually exclusive observation shapes follow, depending on whether the
adapter is a grid world (ARC-style) or a general one (arbitrary JSON state):

- grid: `"n_frames"` (int) and `"frames"` — a list of frames, each frame a
  list of equal-length hex-digit strings (one hex nibble per cell, so colors
  are 0–15; row-major, `frames[i][y][x]`).
- general: `"observation"` — the adapter's `data` object, JSON-serialized
  with sorted keys for determinism.

A paid action (`counts_action: true`, `action != "RESET"`) additionally
carries the prediction and its grade — unless it was journaled through a
path that bypassed the gate, which is exactly what §5 detects:

| field | type | meaning |
|---|---|---|
| `predict` | str | the raw prediction string, redacted (§6) |
| `predict_ok` | bool or null | `false` = a claim missed, `null` = a claim was invalid/ungradable, `true` = every claim graded and passed — see `CLAIM_GRAMMAR.md` §4 for the exact derivation from `grade` |
| `grade` | array | one record per parsed claim (not counting `note`-kind or `aggregate`-kind claims, which never appear here); record shape is `CLAIM_GRAMMAR.md` §3 |
| `mutation_id` | int | the broker's own sequence number for this step, used for crash recovery; not part of the integrity verdict |
| `declares` | object, optional | `worst_case`/`recovery` text for a destructive action, redacted (§6) |

`RESET` events never carry `predict`/`predict_ok`/`grade` — resets are not
predicted, by construction, and are excluded from gate coverage (§5) for
that reason, not because they were bypassed.

## 3. The hash chain

The chain covers **raw journal lines exactly as appended** — the JSON text
bytes, not a reparsed/re-serialized form. This is the v1 chain's central
limitation, and it is why v1 cannot support redaction after the fact (§8).

```
CHAIN_SEED = "assay-chain-v1"          # doubles as this chain's version tag
head_0 = sha256(CHAIN_SEED)
head_n = sha256(head_{n-1} || line_n)  # || is byte concatenation, no separator
```

`line_n` is the exact bytes of journal line `n` (0-based), *without* its
trailing newline, UTF-8 encoded. Blank lines are skipped (append is atomic
and fsync'd; the journal should not contain them, but a reader tolerates
them the same way the writer's own recompute does). `chain.json` stores
`{"event_id": <id of the last line folded in>, "head": <head_n hex>}` — a
convenience cache, not authoritative. **The verdict comes from recomputing
head_n directly from `events.jsonl` and comparing**, so a reader needs
`chain.json` only to know what the daemon *claimed* at some past point
(useful for spotting exactly where a divergence starts), never as a
shortcut that skips the recompute.

A journal with zero events has `head_{-1} = sha256(CHAIN_SEED)` and no
`chain.json` (or `chain.json` absent is itself unremarkable pre-event-0).

## 4. Contiguity

Event `id` must equal the 0-based line number it appears on: line 0 is
`id: 0`, line 1 is `id: 1`, and so on, with no gaps and no repeats. A reader
that finds `events[i]["id"] != i` for any `i` stops trusting event ids
from that point on and reports the run non-contiguous — deletion or
reordering inside `events.jsonl` is exactly what this catches, and it is a
distinct signal from the hash chain (a non-contiguous file can still hash
to a chain-consistent head if nothing was *edited*, only reordered or
truncated in a way that still concatenates to the stored head — contiguity
and the chain are complementary checks, not redundant ones).

## 5. Gate coverage (the "ungated" scan)

An event is **ungated** — the pre-registered bypass definition — when all
of the following hold:

- `counts_action` is true,
- `action != "RESET"`,
- it carries none of: a non-empty `predict`, a non-null `predict_ok`, a
  non-empty `grade`.

In prose: a paid, non-reset action that shows no sign of ever having gone
through prediction and grading. **One ungated event invalidates the run for
scoring** — trust earned after it is demoted, because there is no code-level
guarantee anything after that point went through the gate either.

## 6. Predict/grade consistency

This is a bookkeeping check computable from the journal fields alone — it
is not a re-grading of claims against frames (that needs the grading engine
and is explicitly out of scope for v1; see §9). For every event carrying a
non-empty `grade`, recompute the expected `predict_ok` from the `grade`
array using the rule in `CLAIM_GRAMMAR.md` §4, and compare against the
journaled `predict_ok`. A mismatch means the two fields were not produced
together by one consistent process — most plausibly, `predict_ok` was
edited (or `grade` was) after the fact.

## 7. Anchors — external tamper evidence

An anchor line `{"event_id": n, "head": h}` claims: "at the time this was
written, replaying `events.jsonl` lines `0..n` inclusive through the chain
in §3 produced head `h`." To check one:

1. Recompute the chain (§3) over lines `0..n` only (stop after folding in
   line `n`).
2. Compare to `h`.
3. A mismatch means `events.jsonl` was edited **at or before** line `n`
   *after* this anchor was recorded — the local chain and `chain.json`
   can be made self-consistent by an attacker who controls the run
   directory, but they cannot also rewrite an anchor that lives elsewhere.

A run with no anchor file (or none reachable) is not automatically
suspect — anchors are periodic (every 25 events) plus on WIN, so a short
run may have none. Absence of an anchor means "unanchored," not "invalid";
only a *mismatched* anchor invalidates.

## 8. The verdict

```
invalid_for_scoring =
       ungated_events is non-empty
    OR contiguity check failed
    OR chain recompute != chain.json's stored head (when chain.json exists)
    OR the latest reachable anchor's head != the recomputed prefix head
    OR (v1-verify addition, §6) any predict/grade mismatch
```

Anything else is **CLEAN**. This is the same verdict the kernel's own
`assay audit` computes (`src/assay/integrity.py`) minus the last condition,
which `assay audit` does not currently check — the standalone verifier
in `verify/` adds it, and both implementations agreeing on every fixture in
`verify/fixtures/` (see that directory's `expected.json` files) is the
acceptance test for this spec being an accurate description of the format.

## 9. Explicitly out of scope for v1 verification

**Full claim re-grading** — re-executing a `verify:PATH.py` program or
re-deriving a `cell`/`move`/`region`/channel claim's truth value from the
journaled frames/observations — is not part of what a v1 verifier checks.
It would require reimplementing the grading engine (`predictions.py`,
`channels.py`, `verifiers.py` — closed, layer 3). A third party who wants
full re-grading has everything needed to do it by hand in a shared run
directory: the frames/observations are in `events.jsonl`, channel
definitions are in `.assay/channels.json` (channel declarations), and
verifier programs are journaled alongside their SHA-256 (`verifier_hash`).
v1 verification is the integrity/gate-coverage story: tamper-evidence, gate
coverage, and internal consistency — not "were the claims true."

**Journal sharing policy** is a separate, later decision and is out of
scope for this document. This spec describes the format; it does not say
which journals get published, to whom, or in what redacted form. See §8 for
why redaction and v1 chain verification cannot both apply to the same file
today.

## 10. Spec v2 (reserved, not implemented)

The v1 chain (§3) hashes raw journal lines, so a scrubbed/redacted line no
longer reproduces the chain that was anchored against the original — under
v1, "redacted" and "chain-verified" are mutually exclusive for the same
journal. This is deliberately reserved, not designed in detail yet:

**v2 direction**: chain over a *canonical line form* in which free-text,
agent-authored fields (`note`, `predict`'s free-text remainder, `declares`
values, verifier source) are represented by a salted hash rather than their
raw value. A journal can then have its prose redacted post hoc — replace
the prose with `[REDACTED]`, keep the salted hash alongside it — and the
chain still recomputes correctly over the canonical form, because the
canonical form never contained the prose in the first place. The salt
prevents a dictionary/rainbow attack against the hash guessing back the
redacted text.

v2 would use a distinct chain seed (not `assay-chain-v1`) so a verifier can
tell which spec version a journal follows by which seed reproduces its
stored head — no separate version field is needed; the seed already is one.
Nothing about opening layer 1 (this spec, the claim grammar, the standalone
verifier, or its synthetic fixtures) requires v2 to exist first: v1 is
sufficient for verifying journals that are shared unredacted (e.g. under a
private/NDA-class sharing arrangement), and the v2 slot is reserved here
purely so that freezing v1 today does not foreclose it later.

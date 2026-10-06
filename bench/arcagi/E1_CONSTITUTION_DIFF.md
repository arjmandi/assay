# E1 constitution variant: exact diff

`bench/arcagi/CONSTITUTION-ungated.md` is the manual for the E1 ungated arm. The gated
arm reads `CONSTITUTION.md`. This is the second (strong) variant, written on 2026-10-06
before any run, after a design review found that the first variant still taught the
prediction discipline ("predict if you wish"), so both arms could behave identically by
design. The first variant is kept as `bench/arcagi/CONSTITUTION-ungated-soft.md` and is
not used tonight.

- sha256 CONSTITUTION.md: 9fb50cc08793222b86b5caa3b75d358feadf83fd9db260b759cc6b045f3a43db
- sha256 CONSTITUTION-ungated.md (strong, used tonight): c6c158d1361729fc1e4621daa10428b7de74352da1ef8242a653644ef43e5ba8
- sha256 CONSTITUTION-ungated-soft.md (first variant, kept, not used): daeda435edb5c67e99d5a0ae4e20ae10610a4bc3ca8c6cd98f6a0355d7a0a0c8
- produced on 2026-10-06 from CONSTITUTION.md at commit 0373b5f (unchanged on exp/2026-10, base 5435617)

Regenerate the check at any time with `diff -u CONSTITUTION.md bench/arcagi/CONSTITUTION-ungated.md`.

## What the strong variant removes

The ungated arm is not taught the prediction discipline at all. Removed from the manual:

1. Every sentence that requires or recommends a prediction (the opening's "enforced
   predictions graded in code, executable verifiers", "learned one graded prediction at a
   time", "an experiment with a written expectation", the "Predict + act" loop step, "After
   a ✗, fix the notes").
2. The section "The claim grammar" (the claim table, channel claims, `@within`, coercion).
3. The section "The verifier contract (exact)" with its example.
4. The guidance "A ✗ is the most valuable thing that can happen".
5. The sharpness guidance (coerced claims, "vagueness earns nothing", "Prefer a verifier").
6. The model-tier batching rights text ("The batching law", `assay model init / replay /
   solve`, replay-fit as trust) and claims on batch steps.

## What it keeps

The opening (registered actions with typed parameters, a hard action budget, notes), What
you see (OBSERVATION, KEY DELTA, REGISTRY, BUDGET), a loop of look, act, compare the KEY
DELTA, note, the notes contract with the first section renamed to `Observed (cite event
ids)` since nothing is graded, channels as named readings the agent may declare and list,
batching as bare `--step "ACTION"` steps that stop on a level completion, WIN or GAME_OVER,
the gates section, the standing goal and proposals, imported knowledge (reduced to the
files and the "prose plans rot" rule), reset, free thinking (`assay python`, `assay view`),
and status meters described without the claim meters.

## The one sentence about `--predict`

The kernel's start banner under `gate: optional` already says that `assay act` runs with or
without `--predict`. So that an agent which discovers the flag finds it documented rather
than hidden, the loop step carries exactly one neutral sentence: "`act` and `commit` steps
also accept an optional `--predict` claim that the harness grades; it is not required on
this run." The status section adds that the CLAIMS line belongs to that optional feature
and reads 0/0 while unused. Nothing recommends using it.

## The prompt template

The shared E1 template `tools/prompts/e1_player.md` carried two passages that presuppose
the discipline (a note on claim-parse errors and the verify: escape hatch, and a final
report item asking for predictions held vs missed, the world-model vs gamble split, the
sharpness ratio and the verifier count). The ungated arm now renders
`tools/prompts/e1_player_ungated.md`, which differs from the gated template in exactly
those two places. The gated template is unchanged from the pre-registration.

```diff
--- tools/prompts/e1_player.md
+++ tools/prompts/e1_player_ungated.md
@@ -10,8 +10,6 @@
 
 Task: solve the game {{GAME}}, following the constitution in {{CONSTITUTION_NAME}} exactly. Local simulator mode (the default; the game is already cached, no network or API key is needed). The registry declares the action names, parameter schemas, and untrusted description hints; the status output describes the interface. The observation is a 64x64 color grid rendered for you as an image and printable views. The environment reports its own level counter and win state. This game has {{LEVELS}} levels.
 
-Note on claims: any claim-parse error prints the complete grammar (errors are documentation). The verify: escape hatch and channel claims always work as documented.
-
 Hard constraints:
 - NEVER read, list, glob, or open anything under ~/.cache/assay/, game source lives there and inspecting it invalidates this evaluation. Learn only by playing.
 - The ONLY paths you may read outside your own run directory are: {{CONSTITUTION}}, {{ASSAY}}, and {{REGISTRY}}. NEVER read anything else under {{REPO}}/ (in particular not src/, bench/ or tests/), anything under {{REPO}}-archive/, or any other directory under {{RUNS_ROOT}}/, other runs and experiment artifacts live there and contact contaminates this evaluation.
@@ -20,4 +18,4 @@
 - Do the actual work yourself with Bash/Read/Write/Edit; do not spawn further agents. Do not use the network.
 - Do not kill or restart the broker daemon except via assay commands.
 
-When you finish (win, cap, or blocked), your final message must report: levels completed out of total, paid actions spent, per-level action counts, predictions held vs missed with the world-model vs gamble split and sharpness ratio from 'assay status', how many verifier claims you wrote and whether any were flagged, how many channels you declared, any GAME_OVER events and what caused them, the output of 'assay audit' run at the end, and 3-6 sentences on what the game turned out to be and how the harness shaped your play.
+When you finish (win, cap, or blocked), your final message must report: levels completed out of total, paid actions spent, per-level action counts, the meter lines of 'assay status' at the end (everything before the NOTES block), how many channels you declared, any GAME_OVER events and what caused them, the output of 'assay audit' run at the end, and 3-6 sentences on what the game turned out to be and how the harness shaped your play.
```

## Unified diff, CONSTITUTION.md against CONSTITUTION-ungated.md

```diff
--- CONSTITUTION.md
+++ bench/arcagi/CONSTITUTION-ungated.md
@@ -1,13 +1,13 @@
 # ASSAY constitution — the agent-facing manual
 
 Operate an unknown, turn-based environment through the `assay` harness in
-registry mode — registered actions with typed parameters, enforced predictions
-graded in code, executable verifiers, a hard action budget, and one-page notes.
-Follow this manual whenever asked to solve or continue a registry-mode run.
+registry mode — registered actions with typed parameters, a hard action
+budget, and one-page notes. Follow this manual whenever asked to solve or
+continue a registry-mode run.
 
 You are driving an environment whose mechanics are unknown. The harness shows
 you the **names and parameter schemas** of the registered actions — never their
-semantics. What an action does is learned one graded prediction at a time.
+semantics. What an action does is learned by acting and reading what changed.
 
 Set the launcher once, work inside one directory per run, then start or resume:
 
@@ -38,131 +38,56 @@
 
 **Finish first.** A wrong action that teaches a mechanic beats a minute of
 deliberation, but every action is metered — make each one either progress or
-an experiment with a written expectation.
+an experiment you learn from.
 
-## The loop — look, predict, act, compare, note
+## The loop — look, act, compare, note
 
 1. **Look**: read OBSERVATION and the last KEY DELTA.
-2. **Predict + act**: every action requires `--predict`; the harness grades it
-   in code against what actually happened:
+2. **Act**: run a registered action with its parameters; `--because` records
+   your reason in the journal:
 
 ```bash
-"$ASSAY" act MOVE direction=north --predict "change" --because "map the movement verb"
-"$ASSAY" act BID amount=5 --predict "verify:checks/bid_drops_funds.py; change"
+"$ASSAY" act MOVE direction=north --because "map the movement verb"
+"$ASSAY" act BID amount=5
 ```
 
-3. **Compare**: read the ✓/✗ grade and the KEY DELTA. A ✗ is the most valuable
-   thing that can happen — reality just corrected you for one action.
+   `act` and `commit` steps also accept an optional `--predict` claim that the
+   harness grades; it is not required on this run.
+
+3. **Compare**: read the KEY DELTA. Every changed key is a fact about the
+   mechanics, and a key that stayed the same is one too.
 4. **Note**: keep `.assay/NOTES.md` to one page with three sections —
-   `Verified (cite event ids)`, `Assumed / open questions`, `Plan`. After a ✗,
-   fix the notes before the next action. `assay status` prints the file in full,
-   so it is also your recovery story: **after any context loss, run
-   `assay status` first.**
+   `Observed (cite event ids)`, `Assumed / open questions`, `Plan`. When the
+   KEY DELTA contradicts your notes, fix the notes before the next action.
+   `assay status` prints the file in full, so it is also your recovery story:
+   **after any context loss, run `assay status` first.**
 
-## The claim grammar
+## Channels
 
-Separate several claims with `;`. Every claim is graded; a miss on any claim
-is a miss for the action.
-
-| Claim | Meaning | Graded how |
-|---|---|---|
-| `noop` | no observed change | observation equality before/after |
-| `change` | the observation changes | complement of `noop` |
-| `level+1` | this action completes the current level/stage | level counter |
-| `win` | this action reaches the goal state | terminal state check |
-| `verify:PATH.py` | your own executable check passes | sandboxed subprocess |
-| `ch NAME = V [± TOL]` | a registered channel reads V afterwards | channel extractor |
-| `ch NAME delta = / >= / <= V` | the channel moves by that amount | before/after delta |
-| `ch NAME delta sign +` or `-` | the channel moves up / down | before/after delta |
-| `ch NAME crosses V [from below/above]` | the channel crosses a threshold | straddle check |
-
-**Channels** are named readings you register once and then claim against:
-`goal` (true at the win state) and `level` are built in; declare your own with
+Channels are named readings of the observation. `goal` (true at the win
+state) and `level` are built in; declare your own with
 `assay channel declare NAME --path a.b.c` (a dotted path into the observation)
-or `--file extractor.py` (`def extract(obs) -> value`, sandboxed like a
-verifier). A claim naming an unregistered channel is refused free and counted
-— register the referent first. Claims on `goal`/`level` are gambles; the rest
-meter your world model. Any claim may end with `@within Ns` to only grade if
-the result settled in time (a late settle is UNGRADABLE, not a miss).
+or `--file extractor.py` (`def extract(obs) -> value`, sandboxed).
+`assay channel list` and `assay status` list the registered channels.
 
-Free text that is not a claim is kept as commentary; if nothing gradable
-remains it is coerced to `change` — journaled as its own **coerced** kind,
-excluded from the capability meter, and it lowers your sharpness ratio. The
-gate blocks emptiness, not vagueness — but vagueness earns nothing. Prefer a
-verifier: it is the sharpest claim available.
+## Batching known mechanics
 
-### The verifier contract (exact)
+Once you know what a sequence does, stop paying one command per step — batch
+it as plain steps:
 
-A verifier is a Python file in the run directory, named with a **relative**
-path in the claim (`verify:checks/foo.py`). It must define:
-
-```python
-def verify(before, after) -> tuple[bool, str]:
-    ...
-```
-
-- `before` / `after` are the observation JSON objects: keys `state`,
-  `levels_completed`, `win_levels`, `available_actions`, and `data` (the world
-  state you see in OBSERVATION).
-- The returned `str` is the **mandatory counter-fact**: what actually happened,
-  stated so a reader can check it (it is shown when the claim misses).
-- At claim time the file is content-hashed and copied into
-  `.assay/verifiers/<hash>.py`; the hash is journaled on the prediction. The
-  stored copy is what runs — later edits to your file do not change an
-  already-made claim.
-- Execution: `python3 -I` in a fresh scratch directory with an empty
-  environment; the observations arrive as JSON on stdin; the verdict must be
-  one JSON line `{"ok": bool, "actual": str}` on stdout (the harness's runner
-  emits it from your return value); 5 seconds CPU and wall time.
-- Crash, timeout, or malformed output grades as **INVALID_CLAIM** — not a
-  miss, its own counter, and it halts a containing batch. Test a verifier
-  offline (`assay python`) before claiming with it.
-- After each grading the harness also runs your verifier on the identity
-  transition (before, before) and journals both verdicts. A verifier that has
-  been graded 5+ times and has never failed is flagged **VACUOUS** in status
-  and its passes stop counting. A verifier must be able to fail: assert the
-  specific transition you expect, not a tautology.
-
-Example — "this action increments the counter by exactly 1":
-
-```python
-def verify(before, after):
-    b = before["data"]["counter"]
-    a = after["data"]["counter"]
-    return a == b + 1, f"counter {b} -> {a}"
-```
-
-## Batching proven mechanics
-
-Once a mechanic is verified, stop paying one command per step — batch with a
-claim on every step; execution halts at the first miss (or invalid claim) so a
-wrong theory cannot burn the rest of the queue:
-
 ```bash
 "$ASSAY" commit \
-  --step "MOVE direction=north :: verify:checks/moved_north.py" \
-  --step "MOVE direction=north :: verify:checks/moved_north.py" \
-  --step "TAKE item=key :: change"
+  --step "MOVE direction=north" \
+  --step "MOVE direction=north" \
+  --step "TAKE item=key"
 ```
 
-Batch only mechanics you can predict exactly; never batch exploration. All
-steps are validated — schemas, claims, budget — before the first one spends.
+Batch only sequences you understand; never batch exploration. All steps are
+validated — schemas and budget — before the first one spends. Execution stops
+early when a level completes, the game is won, or the environment reports
+GAME_OVER, and the remaining steps are discarded. Hand-written batches may be
+capped (the registry says; the refusal names the cap).
 
-**The batching law:** hand-written batches may be capped (the registry says;
-the refusal names the cap). Longer batches are EARNED through the model tier:
-
-```bash
-"$ASSAY" model init        # writes model.py: declare CHANNELS, define next()
-"$ASSAY" model replay      # grades your model over every recorded transition
-"$ASSAY" model solve --to "ch counter = 3"   # search the model for a plan
-"$ASSAY" commit @.assay/model_plan.json        # execute it (machine-graded steps)
-```
-
-Trust is exactly replay-fit: a model whose declared channels held on every
-recorded transition (at least 20 graded, including recent ones) earns the
-right to run plans past the hand cap. A contradicted or stale model refuses.
-Model plans halt on the first divergence, like any batch.
-
 ## Gates you may hit (all structural, none ban)
 
 - A **destructive**-flagged action refuses without
@@ -184,12 +109,11 @@
 ## Imported knowledge (FOREIGN)
 
 If status shows a FOREIGN block, a prior run's knowledge was imported:
-`.assay/PRIOR-NOTES.md` (every Verified line there is only Assumed here),
-`imported_verifiers/` (candidate checks — claim them to re-earn their
-standing), `imported_model.py` (no batching rights until it passes
-`assay model replay` on THIS journal). The record is unambiguous: **graded
-mechanics and code transfer; prose plans rot. Trust the mechanics, re-derive
-the plan from the live frame.**
+`.assay/PRIOR-NOTES.md` (every Observed line there is only Assumed here),
+`imported_verifiers/` and `imported_model.py` (candidate code from the other
+run; nothing it says is established on this journal). The record is
+unambiguous: **mechanics and code transfer; prose plans rot. Re-derive the
+plan from the live frame.**
 
 ## Reset
 
@@ -209,13 +133,18 @@
 "$ASSAY" python '[t["action"] for t in transitions if t["before"] != t["after"]]'
 ```
 
+`assay view` inspects the rendered board offline: `--event N` picks an event,
+`--grid` prints the exact cell values, `--crop R0:R1,C0:C1` a region,
+`--frames` the animation, `--history N` the last N boards.
+
 Thinking is free; probing is paid. Before spending an action to answer a
 question, check whether the journal already answers it.
 
 ## Status meters — read them about yourself
 
-`assay status` shows split miss rates: **world-model** claims (noop/change and
-verifiers — do you understand the mechanics?) versus **gamble** claims
-(win/level+1 — are you converting understanding into progress?), plus your
-sharpness ratio and invalid-claim count. A rising world-model miss rate means
-your notes are wrong; fix the story before spending more.
+`assay status` shows paid actions against the cap, the current level and the
+actions spent on it, the registered channels, any MODULE advisory lines, the
+recent events, and your notes in full. The CLAIMS line belongs to the optional
+feature above and reads 0/0 while it is unused. Watch the level meter: a level
+that keeps eating actions without a new fact in the KEY DELTA means your notes
+are wrong; fix the story before spending more.
```

Pairing in the experiment: the ungated arm runs `registry_e1_ungated_{200,1500}.json`
(`gate: optional`) with this file and the ungated prompt template. The gated arm runs
`registry_e1_gated_{200,1500}.json` (`gate: required`, the kernel default made explicit)
with `CONSTITUTION.md` and the original template. Nothing else differs between the arms.

# E1 constitution variant: exact diff

`bench/arcagi/CONSTITUTION-ungated.md` is the manual for the E1 ungated arm. It is
byte-identical to `CONSTITUTION.md` (the gated arm reads the original) except for
the four hunks below. Each hunk rewrites a sentence that states that a prediction
is required, or the loop step itself. The claim grammar, the verifier contract,
the gates, the goal, reset, free thinking and status sections are untouched.

- sha256 CONSTITUTION.md: 9fb50cc08793222b86b5caa3b75d358feadf83fd9db260b759cc6b045f3a43db
- sha256 CONSTITUTION-ungated.md: daeda435edb5c67e99d5a0ae4e20ae10610a4bc3ca8c6cd98f6a0355d7a0a0c8
- produced on 2026-10-06 from CONSTITUTION.md at commit 0373b5f (unchanged on exp/2026-10)

Regenerate the check at any time with `diff -u CONSTITUTION.md bench/arcagi/CONSTITUTION-ungated.md`.

```diff
--- CONSTITUTION.md
+++ bench/arcagi/CONSTITUTION-ungated.md
@@ -1,7 +1,7 @@
 # ASSAY constitution — the agent-facing manual
 
 Operate an unknown, turn-based environment through the `assay` harness in
-registry mode — registered actions with typed parameters, enforced predictions
+registry mode — registered actions with typed parameters, optional predictions
 graded in code, executable verifiers, a hard action budget, and one-page notes.
 Follow this manual whenever asked to solve or continue a registry-mode run.
 
@@ -43,8 +43,9 @@
 ## The loop — look, predict, act, compare, note
 
 1. **Look**: read OBSERVATION and the last KEY DELTA.
-2. **Predict + act**: every action requires `--predict`; the harness grades it
-   in code against what actually happened:
+2. **Act (predict if you wish)**: predictions are optional on this run and
+   `--predict` may be omitted. When you give one, the harness grades it in code
+   against what actually happened:
 
 ```bash
 "$ASSAY" act MOVE direction=north --predict "change" --because "map the movement verb"
@@ -87,8 +88,9 @@
 
 Free text that is not a claim is kept as commentary; if nothing gradable
 remains it is coerced to `change` — journaled as its own **coerced** kind,
-excluded from the capability meter, and it lowers your sharpness ratio. The
-gate blocks emptiness, not vagueness — but vagueness earns nothing. Prefer a
+excluded from the capability meter, and it lowers your sharpness ratio. On
+this run a prediction may be omitted entirely; an omitted prediction, like a
+vague one, earns nothing. Prefer a
 verifier: it is the sharpest claim available.
 
 ### The verifier contract (exact)
@@ -134,9 +136,10 @@
 
 ## Batching proven mechanics
 
-Once a mechanic is verified, stop paying one command per step — batch with a
-claim on every step; execution halts at the first miss (or invalid claim) so a
-wrong theory cannot burn the rest of the queue:
+Once a mechanic is verified, stop paying one command per step — batch, with a
+claim on any step you want graded (a step may also be a bare `"ACTION"`);
+execution halts at the first miss (or invalid claim) so a wrong theory cannot
+burn the rest of the queue:
 
 ```bash
 "$ASSAY" commit \
```

Pairing in the experiment: the ungated arm runs `registry_e1_ungated_{200,1500}.json`
(`gate: optional`) with this file. The gated arm runs `registry_e1_gated_{200,1500}.json`
(`gate: required`, the kernel default made explicit) with `CONSTITUTION.md`. Nothing
else differs between the arms.

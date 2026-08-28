# ASSAY

ASSAY is a **referee harness** that sits between an agent (a person at a
terminal, or an LLM agent) and a world you register:

- **Enforced predict-before-act** — the agent is never told what the
  registered actions do; every paid action requires a checkable prediction,
  validated before anything is spent.
- **Code-graded claims** — every prediction is graded in code against what
  actually happened: change/noop, level and win gambles, executable
  verifiers, and named channels with delta/threshold claims.
- **Hash-chained journals** — everything lands in an append-only journal with
  a rolling hash chain and external anchors; `assay audit` recomputes
  integrity from the artifacts alone, and any world contact that bypassed the
  gate marks the run invalid.
- **Memory + agency layer** — knowledge export/import between runs (imported
  knowledge lands FOREIGN and must be re-earned), a standing goal with a
  proposal lane the agent cannot self-ratify, behavior modules, and safety
  gates (destructive, approval, budget, liveness).

You supply two things: a **registry** (a JSON contract of what the agent may
do — names, typed parameter schemas, budgets, flags, the goal) and an
**adapter** (one Python file plugging ASSAY into your world). See `GUIDE.md`
for the user guide, `CONSTITUTION.md` for the agent-facing manual, and `bench/`
for benchmark harnesses and results.

The journal format (`JOURNAL_SPEC.md`) and the prediction claim grammar
(`CLAIM_GRAMMAR.md`) are published, versioned specs: precise enough that a
third party can write their own reader. `verify/assay_verify.py` is a
standalone, stdlib-only reference implementation — it imports nothing from
this repo's kernel — that recomputes a journal's integrity verdict (hash
chain, gate coverage, predict/grade consistency) from artifacts alone.
`verify/fixtures/` has a clean synthetic journal plus one deliberately
broken in each way the verifier detects. See `VERIFY.md` to run it yourself
in about five minutes.

## Quickstart (60 seconds, no API keys)

```bash
ASSAY=<repo>/bin/assay
mkdir demo && cd demo
"$ASSAY" start counterdemo \
    --adapter "<repo>/examples/counter_world.py:factory" \
    --registry "<repo>/examples/example_registry.json"

"$ASSAY" act INC amount=1 --predict "change"        # graded ✓
"$ASSAY" act NOOP --predict "change"                # graded ✗ — with the counter-fact
"$ASSAY" channel declare counter --path counter     # register a named reading
"$ASSAY" act INC amount=2 --predict "ch counter = 3; win"   # WIN
"$ASSAY" audit                                      # chain + integrity verdict
```

## Status

v1-rc1. Private; no license granted yet.

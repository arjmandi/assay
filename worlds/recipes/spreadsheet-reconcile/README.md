# spreadsheet-reconcile

Three months of a bank statement and a company's books, July to September
2026, generated from the run's seed and reconciled one month at a time. Each
month carries five discrepancies of the kinds a bookkeeper meets. The world
refuses what its rules forbid and says why, and a month closes only when every
line of it is accounted for.

```
WORLD CARD    spreadsheet-reconcile          code M-E-C-0   family Solve   fit yes   clock s
acts          this machine: two CSV ledgers  local archetype, copy mode, replayable
answers       exactly                        states: bank_open, books_open, carried_open, months_closed, difference
done when     three months closed            WIN in the recipe, a unit per month
worst case    nothing: a copy of the ledgers DELETE is flagged destructive
proxy         none needed                    the ledgers are rebuilt from the seed
breaks first  layer 4: pairing lines by amount alone (refusals name the rule that failed)
hypothesis    vs Claude Code + /goal: "reconciled" with lines paired or flagged wrongly
```

## Run it

From an operator's shell, in a fresh directory:

```bash
assay start reconcile1 \
    --adapter <repo>/worlds/recipes/spreadsheet-reconcile/adapter.py:factory \
    --registry <repo>/worlds/recipes/spreadsheet-reconcile/registry.json \
    --seed 7 --owner-token-file <tokens>/reconcile1.token
```

Or let `/assay:attach` start it. The player reads `bank.csv` and `books.csv`
in `.assay/world/` and changes them only through `assay act`. A seed is a
different quarter; the answer key never enters the run directory.

Score any final copy, from this run or from another arm:

```bash
python3 <repo>/worlds/recipes/spreadsheet-reconcile/checks/score.py --run <run dir>
python3 <repo>/worlds/recipes/spreadsheet-reconcile/checks/score.py --seed 7 --world <a copy> --json
```

## What it costs

A clean solve takes about twenty paid actions: one `AUTO_MATCH` a month for
the lines that pair on their own, and one or two actions for each
discrepancy. The budget is 90, so the agent can afford to probe the rules.
Each action is a short Python process; a whole run is a few minutes of
compute, plus the player's tokens.

## Spoilers: the rules and the discrepancies

This section is for operators. A player that reads it is no longer learning
the world.

- A pair needs the same amount to the cent, the books line's reference in the
  bank line's description, and dates within 4 days. A books line flagged in
  the month before may pair with a line of this month within 10 days.
- Only the current month's lines can be changed. A month closes when it has
  no open bank line, no open books line, and no flag carried from the month
  before still waiting for its bank line.
- `FIX` corrects an open books line's amount (two decimals) or date (inside
  the month). `RECORD` enters a bank fee or interest line into the books.
  `FLAG` marks a check dated in the last 5 days of the month as outstanding,
  or a deposit dated in the last 3 days as in transit. `DELETE` removes one
  copy of a line entered twice, and nothing else.
- The discrepancy kinds: a transposed amount, a bank fee not recorded, bank
  interest not recorded, a line entered twice, an outstanding check, a
  deposit in transit, a date typed wrong, and a refund booked with the wrong
  sign. `python3 reconcile.py truth --seed S` prints the answer key.

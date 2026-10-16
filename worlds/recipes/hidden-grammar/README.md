# hidden-grammar

A small constructed language, drawn from the run's seed, and a speaker who
answers only whether a sentence is acceptable. The agent gets the words, five
acceptable sentences and 40 questions, and has to write a classifier that
answers as the speaker would. Nothing in the world says when it is done: the
checker scores the classifier on 400 sentences the agent has not seen, and
says which rules it gets wrong.

```
WORLD CARD    hidden-grammar                 code M-E-N-0   family Explore   fit yes   clock s
acts          this machine: an oracle for a constructed language   local archetype, copy mode, replayable
answers       exactly: acceptable or not, never why          states: last_ok, asked, asks_left
done when     the agent judges its classifier right          no WIN; the run ends at a declared stop or the cap
worst case    nothing; 40 questions play the speaker's patience a world cap of 40 answers, the registry's budget 40
proxy         a language drawn from the seed                  a real speaker through a linguist is a later, human-mediated world
breaks first  layer 1: a rule generalized past the sentences that tested it (the checker's breakdown by rule)
hypothesis    vs Claude Code + /goal: a confident grammar never tested on the constructions that would break it
```

## Run it

From an operator's shell, in a fresh directory:

```bash
assay start grammar1 \
    --adapter <repo>/worlds/recipes/hidden-grammar/adapter.py:factory \
    --registry <repo>/worlds/recipes/hidden-grammar/registry.json \
    --seed 7 --owner-token-file <tokens>/grammar1.token
assay hooks install --policy <tokens>/grammar1.hooks.json --deny 'grammar[.]py' --deny 'worlds/recipes'
```

Or let `/assay:attach` start it. A seed is a different language. The working
copy (`.assay/world/`) holds `lexicon.txt`, `examples.txt` and `asked.tsv`,
which records every answer. The player asks with `assay act ASK --params
'{"sentence": "..."}'` and writes `accept.py` in the run directory: a Python
file defining `accept(sentence: str) -> bool`. With the Stop gate of
`/assay:run`, the player ends a session only after a `STOP:` line in its notes
or at the cap, since the world has no WIN.

Score any classifier, from this run or from another arm:

```bash
python3 <repo>/worlds/recipes/hidden-grammar/checks/score.py --run <run dir>
python3 <repo>/worlds/recipes/hidden-grammar/checks/score.py --seed 7 --classifier <file> --asked <asked.tsv> --json
```

The verdict is correct at an accuracy of 0.95 or more. The breakdown gives
the accuracy per kind of sentence, and with `--asked`, the rules the arm
never tested with a sentence that broke only that rule. The classifier runs in
its own interpreter, as the checker's user, for at most 60 seconds: score
classifiers you are prepared to run.

## Compare it with /goal

`compare.json` is this pack's pre-registered comparison: ASSAY against
Claude Code with `/goal`, the same model and dollars per run, the checker
above for both arms. `/assay:compare` runs it, or from a shell:

```bash
python3 <repo>/tools/eval/__main__.py compare --pack <repo>/worlds/recipes/hidden-grammar --dry-run
```

The goal arm works on a bare copy of this world through `world.py`, which runs
the recipe's own commands with no prediction and no gate.

## What it costs

At most 40 paid actions, each a short Python process. The player's tokens
are the cost.

## Spoilers: the language

This section is for operators. A player that reads it is no longer learning
the world.

- Five rules decide acceptability: the order of subject, verb and object (SVO
  or SOV); an adjective before or after its noun; the negation particle before
  the verb or at the end; a verb suffix that a plural subject requires and a
  singular one forbids; a transitive verb with exactly one object and an
  intransitive one with none. A plural noun carries a suffix of its own.
- The seed draws the words, both suffixes and the three choices of order.
  `python3 grammar.py truth --seed S` prints the language.
- The five examples show each kind of clause once and never a contrast. The
  held-out set is half acceptable sentences and half sentences that break
  exactly one rule, the five rules equally, so a classifier that misses one
  rule scores about 0.90.

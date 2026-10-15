# The world card: how to classify a task for ASSAY

ASSAY grades a prediction before every action the agent takes, so it helps
most where the world answers back: each action has a result a program can
read, and there are many actions to learn from. This rubric decides whether
a task is such a world, and what to build for it. It ends in a world card.

Ask nothing the task's description already answers. Where an answer is
ambiguous, take the more likely reading and name the one ambiguity that would
change a letter.

## Step 0: a world, or a program over worlds

A world is something an agent acts on toward a goal. A program runs over
worlds: a benchmark across models, a change to ASSAY itself, a reporting
format. A program gets no card. Say which parts of ASSAY it would use (the
packs, the evaluation runner in `tools/eval`, the journal and its checker),
and stop.

## The fit check (silent, before any question)

All three must hold. If one fails, say so plainly. Offer a proxy if one
exists (a simulator, a sandbox, an export, an archive). Otherwise recommend
Claude Code with `/goal`, which fits tasks that are short or where nothing
answers back.

1. **The world answers back to a program.** After an action there is
   something a program can read, directly or through a reader such as a
   classifier, or the builder can add an evidence action (Q2 = F). Prefer
   structured readers: JSON from an API, the DOM, test output, a CSV.
2. **Many cheap, graded interactions inform the decisions that matter.** Count
   the probes, not only the final commit. Booking one ride has nothing to
   learn first and fails. A production change after many staging runs
   passes.
3. **The world's clock fits a run.** This version runs worlds that answer
   within seconds to a few minutes (clock s). It runs worlds faster than the
   agent (rt) only through a simulator that waits for it. It warns on worlds
   that answer in minutes to hours (h): one action may outlast the agent's
   tool call, so look for a faster proxy or smaller steps. It declines worlds
   that answer in days or longer (d).

## The four questions

Three options each. Infer the answers from the task, then confirm them on one
screen, with the inferred option first. The letters make the world code,
Q1-Q2-Q3-Q4, for example M-E-C-0.

**Q1. Where does the agent act?** This sets the adapter.

| Code | Option | What it sets |
|---|---|---|
| M | On this machine: files, a code repository, a simulator, a game, an archive replay | the `local` archetype, or `repo` for a repository with tests; the world is a copy |
| S | A remote system a program drives: an MCP server, a REST API, a CLI, a website, an instrument with an API | the `service` archetype, which is not in this version |
| H | The physical world through people: a person carries out the action or reads the result | not in this version; offer a proxy |

**Q2. After one action, can a program read what the world did?** Ask it about
the world's response, not about whether the whole task came out right: a
query with a crisp result is E even when a person judges the final answer.

| Code | Option | What it sets |
|---|---|---|
| E | Exactly | states read from the observation; equality and delta predictions |
| R | Roughly: noisy, slow, or read through a model | tolerances (`± TOL`), `@within Ns`, windowed outcomes (`agg`); a model reader needs a small set scored by people to check it against |
| F | Only the final result can be judged | evidence actions (a claim plus a source the world checks when it is banked) and a sealed scorer at the end |

**Q3. How do you know it is done?**

| Code | Option | What it sets |
|---|---|---|
| C | A check a program can run: a state, a count, a threshold, hidden tests | the WIN rule in the recipe; progress units |
| N | Enough of something has been found | one verified item per unit; the coverage audit before "nothing more exists" |
| P | A person judges | a budget-bounded run and the owner's review |

**Q4. What is the worst a wrong action does?** On this machine (Q1 = M) the
world is a copy, so Q4 is 0 unless the user says otherwise: skip it.

| Code | Option | What it sets |
|---|---|---|
| 0 | Nothing: read-only, a sandbox, a test account, a copy | no flags |
| U | A mess you can undo | `destructive: true` on deletes and overwrites |
| X | Money, the irreversible, people or animals, or a permit | `approval: true` on each such action, a rehearsal on a proxy first, a budget in dollars |

## The tags (inferred, never asked)

- **Clock:** s, h, d or rt, as in the fit check.
- **Proxy:** what stands in for the real system: a sandbox, a dev store, a
  paper account, a staging or test instance, a simulator, an archive. When
  there is one, propose it first; the real system comes later.
- **Scarce commit:** an action with a hard count. The world's act script
  refuses past the cap, or the action is approval-flagged.

## Skip rules and families

- Q1 = M skips Q4.
- Q1 = H or S stops after the card's first line in this version, with a proxy
  offered when there is one.
- F with P is where ASSAY adds least. Say so, and look for a checkable step
  inside the task (a claim with a source, a query, a test).

| | Q3 = C | Q3 = N or P |
|---|---|---|
| Q2 = E or R | **Solve** | **Explore and collect** |
| Q2 = F | **Answer** | **Advise** (ASSAY adds least) |

## The world card

```
WORLD CARD    <short-name>        code M-E-C-0   family Solve   fit yes   clock s
acts          <Q1: the concrete target>          <archetype and mode, replayable or not>
answers       <Q2>                               states: <names to declare at the first event>
done when     <Q3: the concrete condition>       <WIN rule, progress unit>
worst case    <Q4>                               <flags, budgets, a scarce commit's cap>
proxy         <the proxy, or none needed>        <what comes after it>
breaks first  <where it will fail first, and the line of `assay status` that shows it>
hypothesis    <what an agent without the gate would get wrong here, which a checker can show>
```

Where it breaks first, by answer:

- **M** rarely breaks at the adapter. **S** breaks on pagination, rate limits
  and expired auth (world refusals, exit code 3). A website breaks when the
  page view changes on every action.
- **R**: a rising world-model miss rate may be noise or a reader's bias, so
  check the adapter before the agent. **F**: the gamble misses stay
  invisible until the end.
- **N** stops early with a confident "no more": watch self-stops with budget
  left. **C** on a threshold: a wall of spend on one unit. **P** fails
  silently: watch the specificity meter.
- **U** and **X** stall at the gates by design. A run waiting on an approval
  is waiting for the owner, not failing.

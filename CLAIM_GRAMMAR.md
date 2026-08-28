# ASSAY claim grammar — spec v1

Status: **v1**, tied to `JOURNAL_SPEC.md` v1 (the `predict`/`grade` fields
it documents). This describes (a) the prediction-string grammar an agent
writes, and (b) the shape of one graded-claim record as it lands in the
journal's `grade` array — (b) is the part the standalone verifier actually
reads; (a) is here so a third party can understand what produced it, and so
the grammar itself is a published, freezable contract.

## 1. Prediction strings

One `--predict` argument is a `;`-separated list of claims:

```
"cell 12,5=0; region 10:14,3:8"
"ch counter delta = 1; verify:checks/counter.py"
```

Each claim, after trimming whitespace, optionally ends with a validity
window: `<claim> @within Ns` (`N` a positive number of seconds). A claim
whose result settles after `N` seconds is graded `ungradable` (its own
outcome — never a silent pass or a silent miss), regardless of what it
would otherwise have graded as.

A claim that matches none of the recognized forms below, but that *starts*
with one of their keywords (`noop`, `change`, `cell`, `move`, `vanish`,
`region`, `level`, `win`, `verify`, `ch`, `agg`), is malformed and refused
before any spend. Free text matching none of the keywords is not an error —
it is kept as commentary (`kind: "note"`) and never graded.

## 2. Claim kinds

Two claim families exist depending on the adapter's observation shape
(`JOURNAL_SPEC.md` §2): **grid** claims need `frames`; **general** claims
work off any `observation`. A grid-only claim on a general (dict) run is
refused before spend, not silently skipped.

| kind | grammar | family | grades against |
|---|---|---|---|
| `noop` | `noop` | either | nothing visibly changed (grid: zero cells differ and the board didn't resize; general: zero observation keys differ and no level advance) |
| `change` | `change`, or any free text that isn't a recognized keyword | either | the inverse of `noop` — also what an unparsed free-text prediction implies |
| `win` | `win` | either | `state == "WIN"` after the action |
| `level_up` | `level+1` (whitespace around `+` allowed) | either | `levels_completed` increased |
| `cell` | `cell X,Y=V` — `X`,`Y` decimal, `V` one hex digit | grid | cell `(X,Y)` (column, row) equals color `V` afterwards |
| `move` | `move X,Y DX,DY` — signed decimal deltas | grid | the flood-filled same-color object covering `(X,Y)` before the action occupies exactly the shifted cells afterwards, and vacates all its old cells |
| `vanish` | `vanish X,Y` | grid | the flood-filled same-color object covering `(X,Y)` before the action has none of its cells still that color afterwards |
| `region` | `region X0:X1,Y0:Y1` — half-open box | grid | something changed, and every changed cell falls inside the box |
| `verify` | `verify:PATH.py` | either | a user-supplied `def verify(before, after) -> (ok: bool, actual: str)`, run sandboxed; see §3 for the extra fields this attaches |
| `channel_eq` | `ch NAME = V [± TOL]` | either | the named channel's post-action reading equals `V` (numeric compare within `± TOL` if given, else exact/`==`) |
| `channel_delta` | `ch NAME delta (=|>=|<=) V` | either | the channel's numeric reading moved by an amount satisfying the comparison |
| `channel_delta` (sign form) | `ch NAME delta sign (+|-)` | either | the channel moved in that direction |
| `channel_cross` | `ch NAME crosses V [from (above|below)]` | either | the channel's reading crossed threshold `V`, optionally constrained to the crossing direction |
| `aggregate` | `agg ch NAME (mean|min|max) (=|>=|<=) V over Na horizon Na on-fail (advise|revoke_batching)` | either | resolves later, at the stated horizon, over the stated window of prior actions — **never appears in the immediate `grade` array**; see §5 |

`NAME` for channel/aggregate claims is case-folded to lowercase. Values
parse as: `true`/`false` (case-insensitive) → bool; a quoted string
(`'...'`/`"..."`) → str with quotes stripped; else try `int`, then `float`,
then fall back to the raw string.

Every mechanical (non-`note`, non-`aggregate`) claim on an action is
graded; if an action's claims are *only* `note`/`aggregate` (e.g. a bare
aggregate with no accompanying mechanical claim, or pure commentary), the
grammar coerces in an implicit `change` claim (journaled with
`"coerced": true`, kind rewritten to `"coerced"` — see §3) so that every
paid action still commits to *some* checkable, gradable effect. An
aggregate claim is additive only — it is refused outright if it is the
action's *only* claim, before any spend.

## 3. Graded-claim record shape (the `grade` array's items)

Every base claim fields (`kind`, `text`, and the kind-specific fields from
§2's grammar — e.g. `x`,`y`,`value` for `cell`; `channel`,`op`,`value` for
`channel_delta`) are carried into the graded record, plus:

| field | type | present when |
|---|---|---|
| `ok` | bool | always |
| `actual` | str | always — a short human-readable statement of what was actually observed |
| `bucket` | str | always — `"gamble"` (`win`/`level_up`, and `goal`/`level` channel claims), `"world_model"` (everything else mechanical), or `"aggregate"` |
| `window_s` | number | the claim had a `@within Ns` suffix |
| `invalid` | `true` | a `verify:` claim's program crashed or errored — this is `INVALID_CLAIM`, distinct from a miss: the claim never got to assert anything |
| `ungradable` | `true` | the result settled outside a declared window, or a channel/aggregate reading could not be computed (e.g. the channel's path doesn't resolve) |
| `verifier` | `true` | a `verify:` claim (always paired with `verifier_hash`) |
| `verifier_hash` | str (64 hex chars) | the SHA-256 of the verifier program's source, present on every `verify:` claim |
| `identity_verdict` | bool or `"invalid"` | the identity probe's result: the same verifier run on `(before, before)` — used to detect vacuous verifiers, not part of the claim's own grade |
| `excluded_from_meter` | `true` | this `verify:` claim's verifier has passed enough times with zero failures to be flagged vacuous; still journaled, excluded from the capability meter |
| `coerced` | — | not journaled as a field; a coerced implicit claim is journaled with `kind: "coerced"` directly (see §2) instead of a `coerced: true` flag |

A claim kind absent from §2's grid/general applicability for the current
run (e.g. a grid-only claim somehow reaching grading on a general run) is
refused at parse time, before spend — it never reaches this array in that
form.

## 4. Deriving `predict_ok` from `grade` (the consistency rule)

Given one event's `grade` array (excluding, as always, `note`/`aggregate`
entries, which never appear in it):

```
invalid_any = any(item.invalid or item.ungradable for item in grade)
missed      = any(not item.ok for item in grade
                   if not item.invalid and not item.ungradable)

predict_ok = false  if missed
           = null   elif invalid_any
           = true   otherwise
```

`missed` takes priority over `invalid_any`: one real miss makes the action
a `SURPRISE` even if another claim on the same action was also invalid.
`JOURNAL_SPEC.md` §6 uses this rule to check that the journaled
`predict_ok` was actually produced from the journaled `grade` array, not
edited independently of it.

## 5. Aggregate claims are not in this picture

`aggregate` claims open a standing watch at the gate (`over Na horizon Na`)
and resolve later — as their own event content when their horizon is
reached, not as an entry in the triggering action's `grade` array, and not
as part of that action's `predict_ok`. A verifier reading `grade` for
predict/grade consistency does not need to do anything special for
aggregates: they were never in `grade` to begin with, on any event.

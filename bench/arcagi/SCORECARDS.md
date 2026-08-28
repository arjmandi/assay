# ARC-AGI-3 scorecards — every verifiable link

Public scorecards are the only third-party-checkable artifact in this
benchmark: `https://arcprize.org/scorecards/<card_id>`. This file is the index
for ours and for every competitor number quoted anywhere in `bench/arcagi/`.

## ASSAY

**Provenance, stated exactly — read before citing these.** Our agents played
locally (crash-safe journals, hard action caps, one uniform protocol). We did
not capture scorecard ids during those runs, so to keep the record clean we
re-issued the recorded action sequences against the live ARC API and received
fresh cards. Therefore:

- These cards are a **verification replay of recorded action sequences**, not a
  live agent session. The server independently confirms *what the action
  sequence achieves* — outcome, per-level action counts, and the official
  score — on the same game instances.
- **Wall-clock on the card is machine replay time, not agent time.** Real agent
  wall-clock (32–330 min per game, ≈37 h measured over 20 runs) comes from our
  journals and is reported separately in `COMPARISON_TABLES.md`.
- No model is in the loop during a replay; a replay cannot and does not
  demonstrate live reasoning.
- The action sequences are verbatim: an offline dry run confirmed all 25 games
  reproduce their recorded outcome by re-sending every recorded paid action,
  with no skips or edits (8,157 actions total).

| Card | Scope | Link |
|---|---|---|
| `702ccd4f-df1f-4118-bc8b-d79d3f4a1a32` | **the full 25-game set** (consolidated replay, 2026-08-24) | https://arcprize.org/scorecards/702ccd4f-df1f-4118-bc8b-d79d3f4a1a32 |

### What the consolidated card returned

**SCORE 96.54% · LEVELS 177/183 · ENVIRONMENTS 24/25 · TOTAL ACTIONS 8,157.**

The server's own score is **identical to the RHAE we computed offline (96.54)**,
and it agrees per game on every row — 23 games at 100.00, bp35 95.27, lf52
18.18. That is an independent confirmation of both the metric derivation in
`rhae.py` and our per-game action counts, computed by the benchmark itself
rather than by us.

All 25 replays reproduced their recorded outcome exactly (25/25 matched: same
final state, same levels, same action count per game). The card also settled a
small arithmetic error of ours: the true set total is **8,157** paid actions,
not the 8,156 our tables previously stated — journals, actions sent, and the
card all agree at 8,157, and the docs were corrected.

**Validate-path status (arjmandi/me#754):** the 96.54/96.54 agreement above is
currently a read of the card's own rendered SCORE and per-game rows, cross-checked
by eye against `rhae.py`'s offline output — not yet a machine diff of the raw
`GET /api/scorecard/702ccd4f-...` JSON run through `rhae.py --validate`. The two
facts that make the eyeballed match strong evidence rather than a coincidence:
`rhae.py --validate` already reproduces 25/25 *other* published cards' per-game
scores exactly (worst error 0.000000, see `RESULTS.md`), and this card is a replay
of our own recorded action sequences with 25/25 games matching their recorded
outcome and action count exactly — so the same per-level inputs that produced our
offline 96.54 are what the server scored. Closing the last gap needs one fetch this
agent run couldn't make (no `ARC_API_KEY` in this worktree, by design — see
`CLAUDE.md`'s agentd constraints): `GET https://three.arcprize.org/api/scorecard/702ccd4f-df1f-4118-bc8b-d79d3f4a1a32`
with an `X-API-Key` header, save the response as `scorecard.json` in a directory,
then `python3 rhae.py --validate <dir>`. Needs an attended session with API
credentials.

Machine replay wall-clock: 64.8 minutes for the whole set. Agent wall-clock for
the same runs was ≈37 hours measured over 20 runs — the difference is exactly
why the card's timing must never be quoted as agent performance.

Tag breakdown on the card: click 81.93 (7 environments — lf52 sits here),
keyboard 100.00 (4), keyboard_click 100.00 (13).
| `acbe3da6-7219-4516-ba1e-53c97071a615` | cn04 only — an earlier single-game verification replay (its 4.00% total is the set score of a card with 24 environments unplayed, not a cn04 result; cn04 itself reads WIN 6/6 in 223) | https://arcprize.org/scorecards/acbe3da6-7219-4516-ba1e-53c97071a615 |

Earlier per-game verification replays (2026-08-22/23) each minted their own
single-game card; their ids were not captured at the time. The consolidated
card above supersedes them.

**Planned due diligence:** capture the scorecard id automatically at run time so
every future run carries its own card link in its journal, and keep this index
as the monitored record of all cards.

## Competitors — their own published cards

**arc-skill** (Opus 5, uncapped) — one card, all 25 games, RHAE 100.00,
183/183 levels, 7,645 actions:
`24ddb219-987e-464f-9050-6398a29cf5ac`
https://arcprize.org/scorecards/24ddb219-987e-464f-9050-6398a29cf5ac

**Prime Agent** / Prime Intellect (Opus 5) — their published *median* card,
RHAE 95.24, 178/183 levels, 24/25 environments, 11,245 actions:
`2af780b4-f2a1-43e9-a794-b23da3cd3f9f`
https://arcprize.org/scorecards/2af780b4-f2a1-43e9-a794-b23da3cd3f9f
(They report 95.5 Best@1 and 99.97 Best@3 across three runs; the median card is
the one they published for inspection.)

**PRO-LONG** (Fable 5 — stronger backbone; directional context only) — 25
per-game cards, one per game, set mean RHAE 94.71:

| Game | Their score | Card id (prefix with https://arcprize.org/scorecards/) |
|---|---|---|
| ar25 | 100.0% | `32104624-a956-4165-a93e-f342e062338d` |
| bp35 | 74.8% | `04e439cc-5cbf-48c1-8f24-ec67121ae37f` |
| cd82 | 97.4% | `6ea4a89d-7965-411c-af27-31214e700248` |
| cn04 | 100.0% | `86e3b52d-0ef9-4145-aa72-bda4998ddd67` |
| dc22 | 93.6% | `02fa50b8-7daf-48a8-8cc5-b861c3f0f1be` |
| ft09 | 100.0% | `0e1da158-1220-482d-a1f3-a64d16d63962` |
| g50t | 78.4% | `89796e06-f8e2-4744-9ee7-454968742d85` |
| ka59 | 100.0% | `803bb7f8-837d-4b25-828b-8fedb38824dc` |
| lf52 | 81.8% | `61249285-d4e1-49d2-b97a-125211efb50e` |
| lp85 | 100.0% | `bd988f32-9f5d-4c6a-9a0c-9c722822eb78` |
| ls20 | 100.0% | `5a1657ec-dc88-4736-8dc7-ed7c12a6a158` |
| m0r0 | 100.0% | `15eb3ef5-c66c-405a-ba6a-ace06184781e` |
| r11l | 100.0% | `dc76d818-9977-4912-b37c-12634e790d46` |
| re86 | 41.7% | `22d36c56-86d0-4928-bb85-8126d740c434` |
| s5i5 | 100.0% | `44292d23-f556-4919-a2a5-e5d11a541ab2` |
| sb26 | 100.0% | `ec272197-094a-490f-ba02-93bc14d36b86` |
| sc25 | 100.0% | `bcc01315-cf15-4c70-ad3e-70e0a2293975` |
| sk48 | 100.0% | `9d3bb3f0-7b36-48fc-a162-4bd432d2d167` |
| sp80 | 100.0% | `15dd747b-7f89-4565-afc4-8a8a5974ae39` |
| su15 | 100.0% | `0b1fd732-3eb0-486d-9642-9799ea0a53a5` |
| tn36 | 100.0% | `dfaaf21b-2e42-4529-9d23-0f71a4b8df1a` |
| tr87 | 100.0% | `b4cf61c3-abe4-43ea-ae5c-27195347ae2d` |
| tu93 | 100.0% | `ab13922d-e942-449c-ba86-c5e36c75a6b6` |
| vc33 | 100.0% | `b4d93d5d-3d6e-4e9e-89ad-8c598979f49f` |
| wa30 | 100.0% | `2e51aff5-0cd6-42ff-be98-25ca55f221ef` |

Their per-level action counts and baselines (from these cards' JSON) are the
source of `baselines.json`, which makes `rhae.py` reproducible offline.

## Note on card scope

A scorecard spans **all 25 environments**, so its headline score is the mean
over 25 — an unplayed environment counts as 0. A single-game card therefore
shows a low total (see our cn04 card at 4.00%) while the per-environment row
carries the real result. Compare set scores only between cards of the same
scope.

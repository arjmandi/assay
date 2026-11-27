# Evidence pack: ARC-AGI-3, Sonnet 5.5, Competition Mode

A fresh live run on 2026-11-27: Claude Sonnet 5.5 with ASSAY 1.5.1 played all 25 public
games, one run per game, on one Competition Mode scorecard. The model chose every action.

**Scorecard:** [`3e659b9a`](https://arcprize.org/scorecards/3e659b9a-7957-49db-b164-93a76e27134a),
**95.19**, 25/25 environments, 183/183 levels, 11,844 actions
(`competition_mode: true`). Opened 01:15 CET, closed 05:17 CET.

## Contents

For each game `<g>`:

- `journal-<g>.jsonl.gz`: the run journal, one line per action with the frames the game
  returned, same format as [`../arcagi/`](../arcagi/).
- `transcript-<g>.jsonl.gz`: the Claude Code session transcripts: the model's messages,
  every tool call and its result. Hidden thinking text was not saved in this run (Claude
  Code does not keep it by default); session metadata and the account email were removed.
- `notes-<g>.md`: the notes the agent kept for itself during the game.

`scorecard-3e659b9a.json` is the closed scorecard as the ARC API returned it.

## Per game

| Game | Levels | Actions | Score |
|---|---|---|---|
| ar25 | 8/8 | 284 | 100.00 |
| bp35 | 9/9 | 594 | 96.14 |
| cd82 | 6/6 | 179 | 94.01 |
| cn04 | 6/6 | 334 | 100.00 |
| dc22 | 6/6 | 821 | 100.00 |
| ft09 | 6/6 | 94 | 100.00 |
| g50t | 7/7 | 396 | 100.00 |
| ka59 | 7/7 | 470 | 100.00 |
| lf52 | 10/10 | 955 | 100.00 |
| lp85 | 8/8 | 104 | 100.00 |
| ls20 | 7/7 | 815 | 79.72 |
| m0r0 | 6/6 | 277 | 100.00 |
| r11l | 6/6 | 156 | 100.00 |
| re86 | 8/8 | 777 | 100.00 |
| s5i5 | 8/8 | 358 | 100.00 |
| sb26 | 8/8 | 140 | 100.00 |
| sc25 | 6/6 | 421 | 72.08 |
| sk48 | 8/8 | 895 | 94.23 |
| sp80 | 6/6 | 908 | 71.06 |
| su15 | 9/9 | 256 | 99.87 |
| tn36 | 7/7 | 646 | 81.07 |
| tr87 | 6/6 | 342 | 100.00 |
| tu93 | 9/9 | 267 | 91.49 |
| vc33 | 7/7 | 297 | 100.00 |
| wa30 | 9/9 | 1,058 | 100.00 |

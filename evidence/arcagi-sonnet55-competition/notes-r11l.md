# Notes: r11l

## Verified (cite event ids)
- Only ACTION6 click works. Clicking green piece -> it becomes the single black "active" piece (swap recolor) (e5). Clicking elsewhere moves black piece there (e2,e4).
- Blob (pink/purple) = centroid of its group's pieces (e1..e9). Goal: put blob on its matching dotted ring -> level+1 (e9, e41).
- Centroid must not be in white regions (clicks rejected, noop) (e18-e33). Piece itself can go far. Left col-0 black bar = refilled per reset.
- GAME_OVER at e24 after moving piece to (43,57) near red bottom: avoid red zones (cause uncertain).
- Level counter reads levels_completed (level = 1 after L1).

- L1 e9, L2 e41, L3 e73, L4 e100 solved. Clicking within ~3 cells of ANY piece selects it (swap) rather than moving: keep destinations >=5 from other pieces. Only centroid (not pieces) must avoid white. Each group goes to the ring whose colours match its blob; satisfied ring turns black. Blob size can be a 2-colour combo. Extra clicks after level+1 hit next level: check level each step.

## Assumed / open questions
- Whether all rings of a level must be filled.

## Plan
- Per level: compute piece positions with centroid = ring center, keep centroid path out of white/red; click-swap then click-move.
- Helper: /private/tmp/claude-501/click.sh X Y LEVEL (zsh: use ${=a} to split)

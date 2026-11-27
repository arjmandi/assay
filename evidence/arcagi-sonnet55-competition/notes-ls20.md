# Notes: ls20

## Verified (cite event ids)
- ACTION1-4 move 5px cells; bar (row 61 'b' count) costs 1-2/move (blocked moves cost, don't tick sprites); empty bar => life lost/reset; RESET restores everything.
- Indicator (bottom-left) = current colour+shape(+rotation); door box (gray, locked) opens when player stands matching state. Pads (blue lines) teleport; pickups (SH shape-cycle 6 shapes, ROT +90deg, CC colour cycle e>8>c>9) MOVE on 8-tick cycles, tick = successful moves since reset; touch when same cell. Refill squares static.
- Done: L1 18, L2 67, L3 91, L4 125, L5 178, L6 148 actions (L6 done e626). L6 had 2 doors A then B in series.
- Tools: plan.py/tsearch2.py/gen.py/ride.py via `assay python "$(cat ...)"` (time-aware BFS; LEVEL_START var).

## Assumed / open questions
- L7 (start e626): fog circle r~18 around player. Door (colour 8, X.X/XX./.XX = base b2 at rot r2) at bottom (50,29): reach via col-29 corridor (35,29),(40,29),(45,29). Start state: colour c, shape .X./.X./XXX (b3,r0) -> need SH 5 touches (cycle b0..b5), ROT 2, CC 3 (c>9>e>8). Pads: (30,34)->(10,34); (30,39)->(30,29); (20,39)->(40,39). West room (rows35-45, cols9-23) holds SH/CC (row 40) + refill (45,14); reach via col 9: (15,19)L,L,(20..35,9). ROT sprite seen (20,54). Refills (5,9),(5,39),(5,49),(45,14),(20,29). fog.py/spr.py (assay python) build union map & sprite list.

- L7 sprites are STATIC (CC (40,9), SH (40,19), ROT (20,54)); refill squares are consumed once per life. Route in route7.json/c7_*.sh (57 moves then 2 down into door (50,29)); test SH 5x, CC 3x, ROT 2x done in west room then east.

- L7 ROT sprite ping-pongs on col 54 rows 5..30 (period 10, 1 cell/tick); touched at t42 (20,54) and t51 (15,54) via '241' from (15,49) at t48; DON'T walk down col 54 after (extra touches). After 2nd touch: 1,2,2,3 then BFS to (40,29), then 2,2 into door (50,29).

## Plan
- Observe L7 layout, track sprite motion over a few ticks, then time-aware search.

# Notes: bp35

## Verified (cite event ids)
- ACTION3/4 left/right: step 6 cols (first step after turn same). ACTION6 click x,y. ACTION7 undo works (e55, costs 1). 1,2,5 unavailable.
- Gravity UP: player rests on ceiling; camera recenters vertically. Walking under an open cell => auto-rise until solid.
- Cyan blocks vanish on click. Remove block directly above player => rise through open cells.
- PINK in ceiling notches = GAME_OVER if player rises into it (e28). Reset fixes.
- Orange plus = goal; touch (walk onto) = level+1 (e25,e71,e109).
- L3: purple balls toggle on click: solid <-> ghost (x mark, passable) (e74,e81). Solid purple = ceiling stopper / wall. Use to stop rise below pink and open walls.
- Click needs --declare worst_case=.. --declare recovery=.. (hazard module) once game over happened.
- Per level: L1=25, L2=46, L3~38 actions.
- L4: light-blue circle = gravity switch (click flips gravity up<->down; some are one-shot). Gravity down: player stands on floor, click block below to fall. Orange goal may float mid-air: align column and flip gravity so you pass through it. Aligned sprite: 9col = cell_left+1.
- L4=40 actions (2 GAME_OVERs total so far: L2 e28, L4 e123, both from rising into pink).
- L5: used switch becomes an OPEN white cell (passage) and every switch is single-use; switches embedded in gray mass appear only when scrolled into view. Sequence in L5: flip down, descend through block floors, find switch2 (below), flip up, ghosts solid under pinks, rise, return to start row, pass through opened cell. L5=49 actions. Cells are 6 wide: 9col=15+6k.
- L6: clicks only reach visible pixels (camera follows player, ~36 rows). Switch click flips gravity AND opens that cell. Toggle ghosts solid BEFORE ascending past them if you will fall back through that column; ascend via a neighbor ghost column then step beside the shaft. Undo (ACTION7) reverts 1 action each (cost 1). L6=64 actions.
- L7: column of ~16 switches at screen-left = gravity flips on demand (each single-use, anywhere visible). Stable standing needs gray/solid ceiling (g up) or floor (g down); stepping into open column auto falls/rises. Plan cells (6x6) on paper, toggle ghosts to solid as stoppers/platforms, place toggles when visible; camera not always centered near map edges so check each image for coordinates. L7=56 actions (+1 GAME_OVER at e255 rising into pink at top of a shaft).
- L8: pink block (frame, pink fill): click removes it and spawns pink blocks in every EMPTY orthogonal neighbor (incl. above/below). Use as stoppers/floors/ceilings; roll cluster upward (click block above, player rises). Clearing a block next to an empty cell re-spawns there: remove from top, drop onto cover. L8~85 actions, 2 GAME_OVERs.
## Assumed / open questions
- Bottom bar fills 1/action; no limit hit so far.
- L9 solved-route (replayed deterministically from reset; helper ./t.sh "pred" because ACTION params): pink block click spawns pink in all empty neighbours (not into spikes/ghost/cyan); clicking pink above player rises 1 cell. Route e406-e460 (see events): cross from pink block, roll up under cyan strip k=2, spawn pink above hole via k=1 hole, rise Y with pink stopper, build pink ceiling in top row A over R, gravity down by plug (3,61) on wall-top, roll pink down shaft, walk east under stub on pink floors, plug flip up, roll up corridor k7 to purple row, walk west on pink to column of floating cyan block, toggle purple (ghost) to rise under cyan block (e460). e461 DEATH: removing that cyan block rose into spike notches at top (room top = notch row, pink toolkit block at right, cyan wall block at left x=1 in row 2 below spikes = exit to left shaft). Fix: before removing cyan, click toolkit pink (45,3 in e460 view) and roll pink west along row just under notches (stopper row) to above cx4..cx2, then rise to row below, walk west under pink ceiling, click cyan wall block, enter left shaft, then plugs/gravity down to orange pocket at bottom-left.
## Plan
- Read image each level; map ceilings; make ghosts solid under pink; end at orange column.
- zsh: write helper function t(){ ... } not set --.

## FINAL
- GAME COMPLETE at e594 (9/9). L9: top room needs pink stoppers under spike notches; exit via cyan wall block into left shaft; 5 plug/top-switch flips to fall to orange.

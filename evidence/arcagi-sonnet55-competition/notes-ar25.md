# Notes: ar25
## Verified (cite event ids)
- Frames = lists of hex row strings; states ay/ax = min row/col of color 4 (yellow). Cap 16 states used (wasted some).
- Game: a gray dotted shape (5) and its mirror image yellow(4) about a vertical divider (a). Goal: yellow (or mirror) covers dark target (b) silhouette -> level+1 (e23, e47).
- ACTION1/2 up/down move gray+yellow same dir 3px; ACTION3/4 move gray (yellow mirrors) 3px; blocked at walls (e37).
- L2: CLICK (ACTION6) on a gray dot/shape selects gray; click on divider (e43) selects divider; with divider selected ACTION3/4 move divider 3px, yellow moves 6 (e25,e44). Click on gray at a dot (e27) selects gray. Clicks that change selection cost an action.
- Every action ticks the right-edge bar (step counter), clicks without effect don't.
- L3 (e89): objects: gray (movable, dots) with yellow mirror about divider (a). Click a gray dot to select it; ACTION1-4 move selected 3px (divider selected by default / click it). Targets b appear in mirror pairs about divider; put gray on one, mirror covers other. Place divider midway first.
- Verifier files take obs["frames"] directly (no data key).
- L4 (e118), L5 (e147): same idea; with 2 dividers (V and H), copies = orig, V-mirror, H-mirror, both. Decompose target into unit(3px) cells; match copies; set divider axes so mirror copies land on rest. Dividers selected by clicking their dots.
- L6 (e200): target is 4-fold symmetric; unit-grid (3px) printing via ex/u.py (python --file) was the key. Each gray piece has 4 copies; place it in the quadrant whose orientation matches; set dividers to the target's symmetry axes first.
- L7 (e237): extras of mirror copies off-target are fine; target must be subset of union of orbits. Solved via brute force search (ex/solve.py: axes + piece placements on unit grid) -> exact plan.
## Assumed / open
- Wall blocks gray => use parity combos of divider shifts (6) and gray shifts (3).
## Plan
- Each level: compute yellow/gray offset to target; use clicks to select object; batch with ay/ax predictions.

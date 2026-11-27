# Notes: lp85

## Verified (cite event ids)
- Only ACTION6 (click x,y) available. Arrow icons rotate a ring/line of cells; left-half/light-blue = ccw (content moves left on top), right/teal = cw.
- Goal: move piece colored like a target's corner-marker onto that marked cell. Rings can share cells (L3).
- L1 (e5-9), L2 (e10-19), L3 (e19-35) solved by BFS in `assay python --file solveN.py` over piece positions; then ./run.sh "x,y" ... clicks.
- Lattice: cells 2x2 px at stride 3; gray piece idx b(11), purple c(12).

## Assumed / open questions
- Each level: read lattice (lat*.py), model rings as ordered cell lists, BFS, execute.

## Plan
- Level 4+: view image, extract rings, BFS, run.
- L4 (e35-~62): 4 crosses; ALL horizontal arrows = shift 20-ring [TLr,TRr,BLr,BRr]; vertical arrows = ring [TLc,BLc,TRc,BRc]; centers shared. Left arrow=shift left, top arrow=shift up. Solved with solve4.py (BFS).
- L5 (e51-~63): one snake ring (21 cells) rotated by mid arrow pair, plus a short row ring (top arrow pair); empty slots exist; two gray pieces -> two gray-marked targets. solve5.py BFS. Grid scale varies per level (cell 4px stride 6).
- L6 (e62-~88): 3 wheels (8 spokes x3 layers + r4 target). E arrow=radial shift, S arrow=angular rotate; lone arrow swaps r3<->r4 on target spokes (toggle). Solved per-wheel via solve6.py then swap.
- L7 (e83-~93): row ring (8) + col ring (3, shares a cell) + 2x2 ring whose arrows ALSO shift the row (coupled). BFS solve7.py. Level 8 next.
- L8 solved (GAME_COMPLETE). Diagonal+column paths shifted wholesale by bottom arrow; right pairs rotate diagonal prefix sub-rings. solve8.py.

# Notes: g50t
## Verified
- L1-L6 solved (e353). Moves 1-4 = U/D/L/R, 6-cell lattice, walls block. In `assay python` grids are ints (e=14,f=15,b=11); extractors see strings. Tools: ex/lat.py, ex/bfs.py <gridfile> r,c, ex/graph.py, ex/pos.py.
- Cyan small square = plate: hold => cyan gate retracts; release closes. ACTION5 = new run: world resets, old block becomes ghost replaying my moves in lockstep (overlap with ghosts is fine); ghosts hold plates. Max 2 ghosts. reset if recording polluted.
- Gray piston: small end = TOGGLE switch each time something steps on it; big end = gate shifts toward small end. Pink hollow boxes = portals, teleport only on the tick the pink plate is pressed while I'm inside.
- Teal (e) = autonomous patroller on a track, 1 node per my successful move, bounces at walls/closed gates, presses plates (pistons) it passes.
## Plan L7
- view grid, find new element types, plan ghosts with tick timing.
- L7 solved e396: WIN. GAME COMPLETE (396 paid actions).

# Notes: m0r0
## Verified (cite event ids)
- Frames: hex-char strings. States acol/bcol (left/right edge of 'a' cells), arow, acnt.
- Two blocks (color a): ACTION1-4 = up/down/left/right; block A moves as commanded horizontally, block B mirrored (opposite dx), same dy. Wall (6/f colors) -> that block stays, other still moves (e3,e6). Step=block size.
- Hazard cells (color 8 checker): block entering one resets ONLY that block to its start cell (e27, model matched e28-e47).
- Goal: bring both blocks to same row and converge/overlap -> level+1 (L1 e17, L2 e54). Blocks can pass through each other; roles stay with block (A/B swap sides after crossing, so ACTION3/4 reverse effect on sides).
- ACTION5 wasted a move (e16). Move counter bars on row0/row63 (zeros grow).
- Tool: bfs2.py (run in run dir, grid.json from latest frame) finds action plan; use commit with ch acol/bcol.
- L3 (e54-e117ish): color 9 2x2 dots = movable obstacles: ACTION6 click on dot selects it (turns b, blocks turn color 1); then ACTION1-4 move the dot 4 cells; click a block (color 1) to reselect blocks. Dots block blocks; walls 8/f(15) in L3. Clicks/ACTION5 cost moves.
- bfs4.py (MOV=dot idx list, AB='Arow,Acol;Brow,Bcol' block A=moves as commanded, B mirrored) solves with dots; manual pre-moving dots shrinks search. absmap.py draws 4x4 cell map.
- L4: 5x5 blocks, hazard checker resets that block only; dot(9) used as blocker. L5 (e130-e): colored pads: block standing on a pad (4x4 colored tile) holds same-color door open only while standing (non-sticky); orientation of A/B per level must be probed (L5 ACTION4 = left block left). bfs8.py models pads.
- Shared top corridor lets blocks meet across divider.
## Assumed / open
- GAME_OVER from bar exhaustion unseen.
## Plan
- L3+: dump grid.json, adapt bfs2.py, commit.

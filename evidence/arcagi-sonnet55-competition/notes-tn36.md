# Notes: tn36
## Verified (cite event ids)
- Only ACTION6 (click x,y) is available. Top bar (row1) is a per-level action budget: each click, even on nothing, costs 1 cell (~61/level); reset refills (e16).
- 10 toggles under the board (5 horizontal at y=42, 5 vertical at y=45; x=21,26,31,36,41) flip gray<->blue on click (e1,e2,e6). Pristine = G,B,G,B,B on both rows.
- Nothing else changed the board: red circle button (36,55), arch (31,14), U (31,35), cells, wire, corners (e3-e48). Uniform combos then button: nothing (e23,e33,e39).
## Assumed / open
- Board: arch at col4,row1 (b shape), U with nub at col4,row6; goal maybe join them. Unknown how to move.
## Plan
- Try other probes (toggle order/sequence, clicking in sequences), avoid pure waste.
## KEY (found via view --frames, e17,e23,e33,e39): button (36,55) runs program: per column i (top,bottom): (G,G)=arch DOWN 4px, (B,G)=RIGHT, (G,B)=LEFT, (B,B)=noop. Arch (b) piece moves 1 cell/step; if it doesn't finish on U it resets. Goal: land arch on U (col4,row6 => 5 downs). Use `view --frames` to see animation.
## L2 (e62+): right panel = 4 cols x 3 (H,V) pairs, H y=33/39/45, V y=36/42/48, x=40,45,50,55; run button circle (46,58). Moves box (rows24-27) -> goal arch (rows7-11) i.e. needs UP x4. Left icons (12,58)=mode L, (21,58)=mode R: change code->direction map & run left demo panel. mode L: (G,B)=left; mode R: (G,B)=up(left demo). Mapping (G,G)=down,(B,G)=right in mode L.
## L2 findings (e63-e108)
- GAME_OVER at e108: top bar (per-level click budget ~61 incl. toggles/buttons/mode clicks) ran out. Reset refills. Tools: tools/drv.py (setprog, run), box.py/diffs.py via `assay python --file`.
- Right panel col = step; 3 pair rows: p1 (H33,V36): GG=down4px, GB=left, BG=right, BB=noop. p2 (H39,V42): BG=scale x2(!), GG=box jumped left 8 (mirror?), GB=noop. p3 (H45,V48): GB=small in-place shape change (rotate?), BG/GG noop(after).
- Goal L2 guess: box (4x4 with legs on top) is jigsaw complement of arch (rows7-11, cols44-49): needs box top at row 8 = UP 16px. No 'up' code found yet.
- Left panel: demo piece; icons (12,58)/(21,58) switch mode and run left program; left (G,B) moves left in mode L, up in mode R. Right panel not affected by mode (e85).

## L2 SOLVED e356. Right panel opcode per column = 6 bits (h1 v1 h2 v2 h3 v3), G=1: 100000 left,010000 right,110000 down,100001 UP, 001100 left8, 011000 right8, 000100 scale x2, 000010 rotate. Combos non-compositional; brute-force via tools/brute.py (opcode probes, 4 per run) + tools/drv.py (setprog/run/analyze). Left panel+icons = decoration/legend (left toggles dead). Bar ~61 clicks/level: GAME_OVER if empty (reset refills; reset costs 1).
## L3 SOLVED e365 (9 actions): same opcodes; board has wall of magenta w/ gap; planned path on cell grid (4px cells from col33,row4), 1 col per step; XS per level = H-run centers on row 33.
## L4 SOLVED e385. Legend (left icons) reveals opcodes: icon click runs demo & shows pattern in left toggles. shrink=100100 (h1+v2), scale up=000100. Walls: magenta cells block (check gap column exactly: cols 33+4k). Piece may be big; anchored top-left. Check which piece moves first (probe 1 op).
## L5 SOLVED e406. rotate cw=101000, ccw=000010, paint pink=111111 (target pink U needs piece pink + scaled to fit). Order: cw,down3,scale,paint.
Method per level: read grid, find piece/target, plan path on 4px cell grid (walls=magenta), use legend icons (1 click each) to read unknown opcodes. Opcodes: left 100000,right 010000,down 110000,up 100001, shrink 100100, scale 000100, cw 101000, ccw 000010, pink 111111.
## L6 SOLVED. Hatched 4x4 squares = CHECKPOINTS: landing a run on one keeps piece there for next run (also flips orientation 180); otherwise piece resets to start at run end. So multi-run progress possible. Doubles: R2=010100, L2=001100. Legend (left icons) shows basic moves: down 110000, up 100001, right 010000, left 100000. My early L2 'brute' attributions were off by shape ops; use frames analysis carefully.
## L7 SOLVED = WIN (e646). Beam row killed piece when run started from start cell; after landing on hatch (c5,r5) crossing was safe. Route: U,U,R2,R,D (hatch) ; U x4 (hatch c5,r1) ; D,L2,L,U.

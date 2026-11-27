# Notes: vc33
## Verified (cite event ids)
- ACTION6 on maroon(9) valve blocks: click moves fluid (3-4 units) INTO the region the valve sits in, from neighbor across the gray bar. Piece rides fluid surface. Goal: piece marker level with same-colored bar marker. L1 e5, L2 e16, L3 e50, L4 e116, L5 e211, L6 e248.
- Fluid conserved; region cap = min height of adjacent bars (click noop at cap). Noop clicks cost nothing on counter.
- Blue band on a bar lights (c) when BOTH adjacent regions' fluid surface sits exactly at the band's far edge (surface level = band end). Clicking lit band moves piece in adjacent region across; if pieces in both regions they SWAP (e149,e150).
- L5 rotated (fluid from right, bars horizontal); pieces had to swap regions. BFS planner in scratchpad (bfs6.py) worked: model in units of 3.
- Lit rule refined: both adjacent regions' surface col/row == band edge NEAREST the fluid wall (L6: fluid from left, band cols 6-17 -> s=6). Piece geometry caps a region (piece body must not hit walls).
## Plan
- Per level: map regions, valves, bands, markers; BFS on (levels, piece positions); execute with run.sh.

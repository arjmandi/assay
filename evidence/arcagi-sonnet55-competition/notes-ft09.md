# Notes: ft09
## Verified (cite event ids)
- ACTION6 click on a cell cycles its color 9->8->c->9 (palette shown top-right; L1-3 only 9/8). e1-e54.
- Clue (3x3 of 2px cells, center = color X) sits in a grid cell; its 8 neighbor grid cells: clue black(0) => neighbor == X, red(2) => neighbor != X. Overlapping clues are consistent (e53-54 solved L4). Level 1-4 done.
- Helper: /private/tmp/claude-501/solve.py (older); do the constraint solve by hand/script from `assay view --grid > g.txt`.
- L5 (e64-e75): clue 3(green)=off-grid ignore; teal/pink 2-color cells; 'diamond' cells (pink 6 plus-pattern) are switches: click flips self + 4 orthogonal neighbors. Solved with /private/tmp/claude-501/s3.py (args Y0 X0 rows cols, uses g.txt).
## Assumed / open
- L2/L3 had 2 colors so 'different' unambiguous.
## Plan
- L5: parse grid, solve constraints, click, commit with level+1 on last.

# Notes: cn04

## Verified
- ACTION1-4 move the black shape 3px up/down/left/right (e1-e3). Shape = body + 2 light-blue feet (8).
- ACTION5 rotates shape 90deg (down->left->up->right->down), not undoable (ACTION7 unavailable).
- ACTION6 click on shape toggles freeze (magenta, no moves) - avoid.
- Top row bar (color 4) shrinks 1 per action: step limit! Reset refills.
- L1 solved (e45): rotate so feet point right (3 rotations), move so feet land in the 2 light-blue sockets beside the target ring (feet turn green); completes immediately when aligned and body does NOT overlap the ring.

- L2 solved (e~99): several shapes, each with 8-feet. Click (ACTION6) on a shape selects it (arrows/rotate then act on it). Chain shapes by overlapping feet pairs w/ same offsets (feet turn green 3). Do all to complete. In L1 solitary click froze shape (single shape).
- Rotation order down->left->up->right.

- L3 solved (e~140): feet = end cells of shapes (hidden unless selected; select by click to reveal). Shapes link when feet cells coincide; bodies must NOT overlap otherwise. Cycle of 3 shapes needed rotation search: script /private/tmp/claude-501/solve3.py (brute force rotations/translations on 3px cells, origin 2).
- Event frames: assay view --event N --json -> event.frames[-1] (strings of hex chars).

- L4 solved (e185) with generic solver /private/tmp/claude-501/solve4.py + ext.py + pos4.py (extract comps from ev json, rotate, translate, DFS foot matching). Workflow: click each shape to reveal feet, dump events, solve, rotate, then move.

- L5 solved (e284): shape P had black tip cell; click on black tip GROWS P (adds stem cell + arm/foot; 4 clicks -> 7 feet) until a perfect foot matching exists. Click on an already-selected shape DESELECTS it (arrows then do nothing). Overlap during moves is allowed; at the end non-foot overlaps forbidden. Solver: /private/tmp/claude-501/solveN.py (+tryP.py).
- Do predictions with 'change' are weak due to timer pixel; ok.

- L6 solved, WIN at e334. Growth (click tip of SELECTED shape) of two shapes, then solver with ANY placement order (order bug cost much time) found unrotated translation solution.

## Open
- Level 2+: same idea? check.

## Plan
- Find shape, feet, sockets; rotate to face; move by 3s; commit.

# Notes: lf52
## Verified
- Peg solitaire via ACTION6 (click peg, click landing 2 away over a piece; select shows ring on landings). Win per level = exactly 1 cyan (normal) peg left IN THE WHOLE WORLD (L9: other cyan lived on a second board off-screen, reached by riding a node along its rail). L1-L9 done.
- ACTION1-4 slide every node along rails one step (nodes block each other). Purple node = hole cell; a peg that jumps into it rides it. Pink node = stone carrier. Light-blue/dark-red pegs hop but are never captured; they are jump-over pieces, leapfrog pair moves 6px/hop.
- Pitch 6px. Camera follows the node (and scrolls to show off-screen boards). Top bar is an action meter that wraps. Parity: jumps keep (x,y) parity; node moves shift it.
- If level seems unwinnable with visible pegs: ride node along rail to find other boards/pegs.
- Tools: go.sh (key N why / clk x y why), jumps.sh, pj.py (global-coordinate click with camera dx detection via stones; set STONES), solver/l8.py (BFS jumps, edit cells/stones).
## Plan
- L10: view image, find all pegs/boards (ride nodes), BFS.

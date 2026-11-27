# Notes: tu93
## Verified (cite event ids)
- Grid = node graph (3x3 nodes, 6px pitch, red 'o' edges). Arrow move = step player (9/4 block) one node along an edge (e1-e19). Blocked move still costs bar (row 63, ~1/move).
- Goal = cyan(14) node; reach it => level+1 (e19, e35).
- Enemy = color 8 node w/ eye pixel(15) on one side (its front). Entering node adjacent to its FRONT = GAME_OVER (e23); entering enemy node from non-front side kills it (e34).
## Assumed / open
- Gaze range 1 node. Only ACTION1-4 available so far.
## Plan
- tools: map.py/bfs.py in run dir (python via assay python). BFS avoiding danger nodes; kill enemies from side/back first; commit batches.
- Color 12 = patrol sentry: moves 1 node per SUCCESSFUL player move, ping-pong along a column (period 8, e.g. 22,28,34,40,34,28,22,16). Same-node arrival = GAME_OVER (e57,e70). Blocked moves don't advance it. sent.py = BFS over (node, n mod 8). L4 solved e88.
- L5 solved e117: sentries are deterministic bouncers (tri-wave over path range), pass through each other; sent5.py BFS over (node,n mod 140) given sentry fn.
- L6 solved e147 (sent6.py: BFS with static 8-enemies killed from non-front + 12 sentries). Moves are 1 node; n=successful moves.
- L7 solved e163. color13 = another sentry (col bouncer). Blocked moves cost bar ~4 (avoid).
- L8 solved e212: color13 = trail-follower, alerted when I enter its column in LOS; follows my exact trail with lag 2 (never meets me); lure it then circle a loop. Parity: chaser/sentry never adjacent after tick.
- L9 solved e267 = WIN. G in front of enemy needed kills first.

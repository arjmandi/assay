# Notes: wa30
## Verified
- ACTION1-4 move 4 cells (direction change moves immediately; blocked = turn only). Black head=facing. ACTION5 = grab box in front / drop held box in front. Held box follows. ACTION5 on box held by purple steals it.
- Goal: all boxes into red bar. Purple 4x4 helpers autonomously fetch boxes (nearest reachable) and deliver; work in parallel with my moves. Dropped boxes in checker-wall cells = handoff to purple on other side.
- Bottom orange bar = per-level action budget; at 0 -> GAME_OVER (e446, L5 ~125 actions; reset). L1 40, L2 53, L3 80, L4 66 (+81 failed), L5 failed 125.
- Idle waiting wastes budget; do useful staging. Don't block purple in corridor (L5 e327). Held box must be in FRONT when pushing; drag works too.
- Custom extractors time out; use `ch level delta`.
## Plan L5 (reset e446): leave M(28,44) to purple; stage B1(48,52) near mouth, B3, B2, T2(8,56), T1(4,48) near corridor mouth (col 40-44,row 32); avoid idle.
- L5 solved e446-568 after reset: budget really tight (~125 acts/level?). Dragging box over bar into a slot myself works (me walks over empty slots; box trails). Count rows carefully (4 cells/step; adjacency needs exact rows).
- L6 (e568-619), L7 (e619-668): pink 4x4 = ADVERSARY: steals boxes from bar to its own plain-red zone. ACTION5 facing pink adjacent CAPTURES it (vanishes). Then ferry boxes from pink zone to dark-framed bar by drag (box trails on side).
- L8 (started e668): two tiers (top/bottom) each w/ purple helper + pink thief + pink zone (plain red) + dark bar. Top bar 8 slots, bottom 9. Pink zone top starts with 4 boxes. ACTION5 on IDLE pink (not carrying, adjacent, facing) captured it (top pink gone e730); on carrying pink it steals the box. Purple also fetches zone boxes. Chasing a moving pink is futile (same speed); wait for it at its zone/idle spot, approach from the side it isn't facing.
- Attempt1 e668-~790 ended unfinished (bar nearly empty); top bar 6/8 done, bottom 4/9.
## Plan L8 retry: reset; go up through top gap (cols16-23) immediately; capture top pink when idle; ferry top zone boxes; then bottom.
- L8 KEY: ACTION5 on pink = STUN (dot in dark block; if it carried a box it keeps it); a SECOND ACTION5 on the stunned pink REMOVES it permanently (e851-852). Do both immediately. Top pink at start walks (16,32)->(8,20)->(8,16) with a box; stand at (12,16) after: right3,up3,up1,wait1 -> pink arrives above you -> ACTION5 x2.
- NEVER press a direction into a box you face while "drag-walking": drop/hold state confusion cost 14+6 actions twice (box in front blocks you). After dropping a box, step sideways first.
- L8 attempt2 reset again at e876 (top pink never removed).
- L8 SOLVED at e990 (3rd attempt): stun+remove both pinks early, ferry zone boxes myself via row 0 with box trailing BELOW me (grab from above facing down, step up, run right along row 0, step down to push box into slot, drop). Actions per level: L1 40,L2 53,L3 80,L4 66(+81),L5 125(+123),L6 51,L7 49,L8 322 total incl 2 resets.
- Now L9 (last): left big bar 3x3, right checker wall (handoff) w/ 2 small bars, bottom maze w/ pink.

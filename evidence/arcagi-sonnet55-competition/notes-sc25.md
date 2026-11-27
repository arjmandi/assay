# Notes: sc25

## Verified (cite event ids)
- Cyan bar right edge = energy (-2px/effective act), refills on level up.
- Head (4x4 two-tone) moves 4px w/ ACTION1-4 along red corridors; goal = dock head into the maroon/white socket ("base") -> LEVEL_COMPLETE (L1 e32, L2 e38, L3 e57).
- ACTION6 click toggles a cell of the 3x3 grid cyan; when the selected cells == the cells marked on the legend tile (small tile left of grid; marks on black cells), grid turns all red and a "spell" fires that changes obstacles (L1 shrank head to pass narrow channel, L2 removed gray dots blocking, L3 removed decoy socket + mint block). Re-select to recast.
- L3: first cast did nothing visible; recast with head near gate (e46-48) removed decoys.
## Assumed / open questions
- Spell effect may depend on head location/state; recast if nothing happens.
## Plan
- Each level: read legend tile, click matching grid cells, route head to the true socket.
- e138 GAME_OVER: L4 head entered the magenta-framed (left) socket = trap. Real socket = white one right (x51-55,y35-38) behind mint 'd' block (x47-49) - mint removal unknown.
- Pink legend (4 edges) fires at 4th click: toggles head size 4x4<->2x2. Magenta legend (middle column) cast did nothing in L4 (tried head aligned/adjacent, big/small). Cyan 'ee' pixel = energy refill.
- e159 KEY: magenta cast (select middle column N,C,S) removes magenta socket+mint ONLY when head has line-of-sight/row-alignment toward the magenta socket (head in left corridor row 29-30 at x=19). Casts elsewhere do nothing. Energy: 4px per move of 128px; cyan 'ee' = +32 refill.
- L4 plan: reset; down x2 (head rows 27-30), cast column, down x2 (rows 35-38), right x4 into white socket.
- L4 SOLVED (e218). RULE: a cast only fires when head is in line-of-sight (inside the corridor) toward the magenta target; head must be small (pink cast) for 2-tall corridors. Casts toggle/remove coloured obstacles matching legend colour. Magenta socket frame = TRAP (GAME_OVER). 
## L5 notes (e218-e281; GAME_OVER e281 = energy exhausted)
- 3 legends: pink(4 edges)=toggle head size; gray (cells (25,50),(30,50),(30,55))=teleport head to cage: BIG head->right cage (51,35), SMALL head->left cage (29,39); magenta col (30,50),(30,55),(30,60)=removes magenta socket in head's LOS + same-colour block in right corridor (d mint). 
- L5 goal: white socket top-right (x50-55,y10-14), corridor has mint d then purple c (c=twin of bottom-left magenta socket 'cc' behind yellow gate rows 44-45). Cast from shaft (small head cols16-17,y37) removed top-left socket + d (e259). Casts from junction rows 43-44 (small) and 42-45 (big) did NOT remove bottom-left/c. Energy 128px, move=4, cast 4-8; ee at (13-14,40-41) refills.
- L5 SOLVED e328. Cast on bottom-left needed head at corridor mouth (x=11-12,rows 43-44) - LOS = head at the end of the corridor adjacent to gate. Sequence: gray(big->right cage) , pink shrink, gray(small->left cage), left6, down to shaft up1, magenta; down to mouth, magenta; enlarge(pink), gray -> right cage, up 7 = socket.
## L6 (final) starting e328
- L6 (e328-): casts+moves burn energy (128px total, cast~8, move 4, ee refill +32). GAME_OVER e379 = energy 0 at the very end (wasted 2 failed casts + 8 moves to bottom run). 
- L6 efficient plan from reset: pink shrink at start; gray -> left cage (13,41); R,R,U,U to (17,37) [DON'T go up further=magenta socket trap]; magenta cast (kills left socket+mint d); pink enlarge; gray -> right cage (53,37); L,L to x=45 (stay >=4 from magenta socket x38-41); magenta cast (kills c+bottom socket); gray -> center cage (29,33); R,U x5 to top socket.
- Magenta cast only works if NO mint d between head and magenta socket in LOS; mint d is removed by casting at its twin socket (left one).

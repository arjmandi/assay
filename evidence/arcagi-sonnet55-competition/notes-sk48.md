# Notes: sk48 (L7 done e786; 7/8). Now L8
## Verified (mechanics)
- Cells: item 4 wide, pitch 6; bands rows 9+6j (j=0..6), cols 12+6i. ACTION1/2 arm up/down 1 band (L1-5) ; ACTION3/4 retract/extend rope 6 cells.
- Vertical move: arm ENTERS a row -> items overlapped by rope get pushed 1 band (chain-push); select prefix by rope length chosen in EMPTY row; leftmost always included. Top/bottom rows are traps.
- Link = each extend attempt that is BLOCKED (pinned train) links the next item adjacent to tip. Bar holes show correct-slot links; wrong item at slot1 can't be undone (retract drops from tip end only).
- Retract pulls linked items; compress further drops them in place.
- L6: TWO arms: ACTION6 click on arm head selects it (left head grid x=8,y=29; top head x~32+,y=4). Left arm: ACTION1/2 rail, 3/4 rope horiz. Top arm: ACTION3/4 move along top rail (6 cols), ACTION1/2 retract/extend rope down (6 rows).
- L6 layout: wall rows26-31 cols29-34 blocks both ropes. 3 maroons row27 cols36,42,48 (arm1 bar M,M,M); 3 lightblue col30 rows33,39,45 (arm2 bar L,L,L).
## Plan L6 (after RESET)
 P1 arm2 at col c(36/42/48): extend, push M(27,c) down to bottom (45,c) (pinned), next push links M; retract lifts M; compress-drop it at row15. Repeat x3 -> M at (15,36),(15,42),(15,48).
 P2 arm1 pushes L's right to col c (not pinned). P3 arm1 to row15, extend: link M,M,M; retract chain left. P4 arm2 pushes down col c: link L,L,L.

- L6 SOLVED: arm2 pushes M down col c to bottom, pinned link, retract lifts, extra retract drops at row 9, 1 extend pushes it to row 15; 3 M's in row 15 -> arm1 (row15) 3 pinned extends link M,M,M; retract chain left; arm1 pushed L's to col36 earlier; arm2 col36 3 pinned extends link L,L,L => level+1. Tip: dropping an item = retract past minimum.
- Helper scripts in /private/tmp/claude-501/g.sh (act + grid dump).

- L7 SOLVED (e705-786): bars arm1 M,T,M / arm2 L,T,L share one T -> CROSS: T at (row39,col42), M at (39,36),(39,48) row for arm1, L at (33,42),(45,42) col for arm2; items moved by right-pushes (arm1) & down-pushes (arm2); link pinned trains. Same T linked to both works.

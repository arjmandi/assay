# Notes: sp80

## Verified (cite event ids)
- ACTION1-4 move SELECTED platform (color 9) by 4px (can reach x=0 and wall); ~30 actions/life (timer), expiry => GAME_OVER. reset refills & restores layout; ACTION5 5th use per life => GAME_OVER.
- ACTION5 = fire/evaluate (also cycles selection). Click (ACTION6) on a light-blue(8) platform selects it; needs --declare worst_case/recovery. Use ./seq2.sh (C:x:y click, ACTIONn).
- RULES (L1 e415, L2 e~498, L3 e594 all won): every source (pink/yellow block) emits a 4-wide stream straight away from its wall. It stops at the first platform covering its columns, splits to BOTH ends, leaf columns (x-4..x-1) and (x+w..x+w+3) continue straight. Every leaf/source must end in a cup gap (4px pocket); stream hitting prong/ceiling = fail. Wall-edge leaves (x=0 or x+w=64) are fine. Platforms vertically adjacent (<4 row gap) overlapping in cols block lateral flow: keep >=4-row gaps. Merging streams ok.
- L3 win: Y(w20)@(32,20), X(w24)@(12,28), U(w16)@(0,36), B(w24)@(40,44).
- L4 won e631 (cell size 3 there; walls cols0-1,62-63; steps=3px; Q's yellow square is a hole marker that moves with it, harmless if not hit; timer cost/action small in L4). Wrote /tmp/s4.py style brute-force solver of leaf tree.
- L5 won e774: pieces can't touch static cups (need >=1 cell gap). Pink L-tromino (color f) = 90deg MIRROR: rising stream entering its notch (cell under arm, left of leg) turns LEFT horizontally, exiting at notch rows. Sideways cup fed by that. Timer ~95 actions/life in later levels. Helper: comps.py (components via assay python), seq2.sh.
- L6 (current, level 6/6, ~e821): gravity down, source top (cx9). 4 cups: bottom(9,17), UL west row7, LL west row12, R east row10 (cell=3px, cell=(px-2)/3). Pieces: Q 5-cell platform w/ yellow hole, V vertical 4-cell platform, 2 L mirrors. Offline sim in /tmp/l6 (may be lost). 3 tried layouts failed (mirror-solid-as-splitter + hole semantics uncertain).
- L6 WON e908: yellow square inside platform Q is an EMITTER (beam in gravity dir); Q under source splits source beam to both ends; mirror notches turn beams; V splits west beam to UL/LL cups. GAME WON (all 6 levels).
## Assumed / open questions
## Plan
- Each level: read grid (cups, sources, platforms w/ widths), design leaf tree, move pieces, ACTION5 once. Reset first for full timer.

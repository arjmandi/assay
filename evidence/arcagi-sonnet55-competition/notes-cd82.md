# Notes: cd82

## Verified (cite event ids)
- Block (rows34-43, cols27-36) must match target (top-left, rows3-12 cols3-12). ACTION5 sweeps main brush into block: paints half-plane facing brush pos: N ri<=4; E ci>=5; NE ci>=ri; NW ci+ri<=9; SE ci+ri>=9 (ri,ci 0..9). Last stamp wins. (e5..e37, e79, e87)
- Brush pos graph: NW -a3/a4- N -a4- NE; a2: NE->E->SE; a1 reverses (SE->E->NE); a3 NE->N. Limit = free noop.
- ACTION6 click on palette icon (y=4) selects color. L3 palette x=23,29(pink),35(purple),41,47(teal),53(ltblue),59.
- L3: small cap brush above N (cols30-33 rows18-22): ACTION6 click at (31,18) sweeps it -> paints 4x3 rect at block rows34-36 cols30-33 (e106).
- Counter bar bottom = moves; 1 cell per ~action, no game over seen.
- L1 59 acts, L2 15 acts (e73), L3 44 acts (e117).
## Assumed / open questions
- Other small brushes at other positions may exist in later levels (click them).
## Plan
- Read target, decompose into half-plane layers, order stamps, helper: ex/steps.sh (c:<x> color, a1..a5), ex/pos.py (view --grid | python3 -I ex/pos.py).

- L4 23 acts, L5 22 acts. Ring: a2 from NW->W->SW (SW=ci<=ri), a1 reverses. Cap at N and W. Click cap FRAME.

- L6 solved e179: WIN. Total 179 acts.

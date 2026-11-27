# Notes: sb26
## Verified
- ACTION6 click bottom tile selects it; click on slot (red dot in framed box) swaps tile into slot (e1-e8). ACTION5 submits; L1 done e9 (9 actions).
- Goal L1: top row shows target color order; fill slots in that order, then ACTION5.
- Use `X=${p% *}` in shell loops (don't `set --`, clobbers $A). Prefix `export _ZO_DOCTOR=0`.
## Assumed
- Later levels add twists; re-verify.
## Plan
- View each level's image, solve with clicks + ACTION5.
- L2 done e40 (31 actions). Tube from small hollow square = child box inserted in sequence at that position: reading order is flat tree traversal (top c f [lower box 8 9 e b] 6 = palette order). Small square not clickable. Clicking filled slot selects it; selecting+clicking another slot swaps. Bar at row 53 = move budget (~0.5px/click).
- helper /private/tmp/claude-501/c.sh "x y" ... clicks
- L3 done e55 (15 actions). Slots order: parent slot, child slots at child's position in parent's row; nested traversal confirmed.
- L4 done e70 (15). Hollow tile in palette = child-box connector: place in a slot; the prefilled child box then splices there. Flatten order includes prefilled tiles.
- L5 done e87 (17). Same connector can be placed several times; box expands at each (macro). Target length = plain slots + connectors*box size.
- L6 done e105 (18). Multiple child boxes w/ own prefilled tiles; connector colour = box colour.
- L7 done e123 (17). Boxes may nest: connector tile can be placed inside another box.
- L8 done e140 WIN (17). Connector of the root box = repeat root content so far.

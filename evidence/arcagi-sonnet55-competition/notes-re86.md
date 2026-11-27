# Notes: re86

## Verified (cite event ids)
- ACTION1-4 move selected shape 3px (up/down/left/right); ACTION5 cycles selected shape (cursor black pixel = its center) (e1-e5).
- Goal: each shape's lines must pass through ALL yellow squares whose center colour = shape colour. Level1 e25, L2 e61 solved this way.
- Pink bar at bottom shrinks per move (step budget?), resets per level.
- Helper: helper.py prints square centers (x,y) + colours; shape extents per colour.

## Assumed / open questions
- Shape geometry (cross arms +-13, X diag, diamond r=9) may differ later.

## Plan
- Per level: run helper, derive centers by geometry, move each shape in 3px steps, ACTION5 to cycle.
- L4 (e108-e242): swatches (big red-ringed colored squares): shape recolors when ANY of its pixels touches swatch INNER; recolor so shape colour == squares colour. Avoid brushing wrong swatches in paths. Bar is not a real limit so far. L3 solved by brute force (bf.py in scratchpad) over shape translations.
- L5 solved e309: colour groups matter (inner colour 8 = lightblue); use brute force bf5.py approach incl. recolor. Sol'n search: assign each shape one colour.
- L6 solved e376: blue block is immovable; shapes keep fixed total length/perimeter: rect (perimeter const) deforms when pushed (blocked side: opposite edge retracts, perpendicular grows); cross arm pair total length const (blocked tip truncates, opposite arm grows) and asymmetry persists. Cursor = bbox center, not shape center. Squares must lie on shape outline/arms.
- L7 plan (e376): white cross->gray swatch, extend up arm via block-top truncation, center (45,48); purple rect->darkred, squeeze w18 h6 by 2 up-pushes under block, place x39..57,y18..24; orange cross->lblue swatch, truncate left arm at block (left 6/right 30), center (9,15).
- L7 solved e539. Screen edge doesn't truncate (clips); block truncates arm axially; cross arm pair total fixed. Vertical arm of cross under block face-on moves: truncation works when arm tip hits face axially; perpendicular arm vs block = blocked move.
- L8 (last) started e539.
- L8 (e539+): 2 rects (perimeter 48 const), two 5x5 blocks (b1 x58-62,y1-5; b2 x34-38,y55-59). Targets: gray corner-squares need w6,h18 at x9-15,y39-57; magenta squares need w15,h9 at x6-21,y45-54. Swatch row y28-32 (rings, gap x44-48), gray swatch (7,7), magenta (7,22). Touch (even ring) recolors; shapes clamp at screen edge. Push model: horizontal push w-3,h+3 (h grows alternately bottom/top); vertical push w+3(alternately left/right),h-3. Plan: narrow rect to w3 to pass gap x45-48, widen below at b2.
- e573: purple rect B already magenta (w15,h9) at x3-18,y15-24; needs to go down via gap.
- e673: gray rect B done at squares. Learned: ring touches recolor; narrow w3 passes gap x45-48; short w21,h3 at rows15-18 passes band x28-32 above row; pushes alternate growth edges (observed). Now A (white): plan narrow@block2 -> gap up -> flat@block1 -> magenta swatch (7,22) via rows21-24 L<=6 -> back, narrow@block1 -> gap down -> widen@block2 (4 pushes) -> x6-21,y45-54.
- WIN e777 (all 8 levels, 777 paid actions). Done.

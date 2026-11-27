# Notes: dc22

## Verified (cite event ids)
- Dot (cyan) walks 2 cells/step over solid bridge pieces between red nodes; ghost (dither)/gray gap not walkable. Goal: reach red box with gray dot. Dot cannot stand on a piece being moved (toggle/drag refused).
- Right-panel items are clickable buttons (ACTION6): dark hat swaps dark slots; magenta hat teleports dot between the two magenta-icon boxes (dot must be ON icon cell); pink hat (appears after dot visits pink-icon box) swaps pink pieces solid/ghost; L button shifts blue slider +2 cols (period 6); red square cycles gray bar r=0..4..0 (r=4 gray bar fully 'c' walkable).
- L5 (e320-586): lens/gripper on black rail moved by 4 squares (white=left/right, dark=up/down); lb flat item toggles grip when lens at purple tab; gripped purple bar follows the lens along rail (4 cells/step). Used purple as bridge R30-R20, then as bridge pink box<->box1. Don't run act through `| head -N` pipes (SIGPIPE can drop action).
- 'c' color = bridge connected at both ends.

## Assumed / open questions
- L6 combines everything.

## Plan
- Per level: image, identify target, boxes, hats; plan toggles with dot off moving pieces.

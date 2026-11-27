# Notes: s5i5

## Verified (cite event ids)
- Only ACTION6 (click x=col,y=row). Bottom green bar = move budget (some levels).
- Frame w/ two glyphs = button pair for one arm: left glyph shrinks, right glyph grows arm by 3 cells (e8,e9,e24-27). Arms are chained (each arm pushes the later arms). Arms alternate H/V. The dot (d) on the last arm must land on the diamond ring center (L1 e21, L2 e65).
- Walls (pink f) block arm growth (no change, e36). Plan routes through gaps: arm thickness 3, must fit corridors.
- Frame order = arm order in chain; frame colors match arm colors.
- L1: 22 actions; L2: 44 actions (e22->e66).

- L3 solved (multi-dot: each dot-arm needs own diamond; other chains are movable obstacles; solid blocks like pink f block arms; 'no visible change' = blocked move). Plan obstacles first, check collisions of every rigid tip.

- L4 solved: one frame can control SEVERAL same-color arms at once (all grow together). Solution: set lengths to min, rearrange the chain arms (vertical shafts) so horizontal bars occupy distinct rows away from dot path, then grow. Dot within ~3 of diamond center counts. Blocked click = 'no visible change' (don't loop blindly; use halting loop).

- L5 solved: levels can START with a dot already on its diamond (don't disturb it carelessly; may need to shrink and re-grow). Tip rigid pieces have big sizes (check full extent in grid). All dots must be on diamonds. Left glyph=shrink, right=grow still. Cost ~44-55 actions/level when planning thoroughly.

- L6 solved in ~35: '+' frames rotate that arm (and rigidly all later arms in chain) 90deg CCW (down->right->up->left); arm anchor stays. Use rotations to route chain through wall gaps. Dot row = anchor band top+1.

- L7 solved (~90): rotation CCW only, cycle skips self-colliding orientations; rotating an arm out of bounds (up) is allowed (8 parked up). Min-length arms fit in a 3-row corridor. Chain relation: each arm's dir = prev arm's dir rotated CW. Blocked rotate/grow => 'no visible change'. Shrink arms to min before rotating base arms.

- L8 solved (~58): hub with 4 arms rotates together; box walls limit arm growth, rotate hub so needed arm points through the gap. GAME COMPLETE.

## Assumed / open questions
- Click inert frame = noop (free?). 

## Plan
- Per level: map arms/walls/diamond, plan path in free thinking, then click.

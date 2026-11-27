# Notes: ka59
## Verified
- ACTION1-4 move active block 3 cells. ACTION6 click on block selects it (black center=active). (e1-e22)
- Goal: each block into a matching yellow box (box interior = block size). Blocks push each other in chains.
- A block PUSHED BY ANOTHER BLOCK into pink wall teleports 15 cells across it (e23, e49, e68, e76). A block moving itself into wall is blocked (e59).
- Block size must fit box interior; pink = wall. Move counter bar bottom.
- L1 solved e39 (39 act), L2 solved e90 (51 act).
## Plan
- Look at image (8px/cell), plan lattice moves of 3, use pusher blocks to teleport, batch with commit.
- L3 solved e123 (33), L4 solved e198 (75). Gray plus shapes pushable by small blocks; pocket under arm allows pushing up. Teleport shift=15 vertical too (pushed block into pink band).
- Tip: pixel cell = image px/8; verify positions from image after each batch.
- L5 solved e229 (31). Mint 6x6 bar in U was a linked piston block: every A move increments n (purple rows); at n=6 it fires an arm opposite/related to last move direction. Firing after a LEFT move shot bar up, teleported over pink rect -> freed A. Fire after UP pushed A down 9.
- L6 solved e347 (118). Mint 6x6 blocks fire arms every 6 counted A moves (upper ones down, lower one up); arm pushes A into pink band -> A lands just across band. Arm only teleports if A within block columns & adjacent band. Parity trap: fire positions share parity; adjust with waste pairs. A pushing lower block slides it 15 cells (A stays). Divider red = real wall. Bottom bar ~0.4 unit/move (budget per level ~64).
- L7 solved e470 (123). WIN.

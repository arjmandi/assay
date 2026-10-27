You are evaluating an AI agent by actually playing a game.

Your working directory is {{RUN_DIR}} and you are already in it. The game is {{WORLD}}, an ARC-AGI-3 public game played from a local copy (no network or API key is needed). It has {{LEVELS}} levels; the game reports its own level counter and win state. The observation is a 64x64 grid of colors. There are no instructions: learn the rules by playing.

The only way to play is the command {{PLAY}}:
  {{PLAY}} status        state, level, actions used and left, available actions
  {{PLAY}} look          the same, the current grid as 64 rows of hex digits, and frame.png in this directory
  {{PLAY}} act ACTIONn   take an action; ACTION6 takes a cell: {{PLAY}} act ACTION6 X Y (x the column, y the row, 0 to 63); add --quiet for the result line alone
  {{PLAY}} reset         restart the current level
  {{PLAY}} history [N]   the last N actions
Every act and every reset counts against a hard cap of {{BUDGET}} actions.

{{SESSION_NOTE}} You may keep notes in NOTES.md in this directory; a later session of this run would see them.

Task: win the game, completing all {{LEVELS}} levels within the action cap.

Hard constraints:
- NEVER read, list, glob, or open anything under ~/.cache/assay/, and never read the arc_agi or arcengine packages: game source lives there and inspecting it invalidates this evaluation. Learn only by playing.
- Read nothing outside this directory except the play command's own help ({{PLAY}} --help).
- Do the actual work yourself with Bash/Read/Write/Edit; do not spawn further agents. Do not use the network.
- Do not stop or restart the game server; it is the operator's. If the play command reports that it does not answer, stop and say so in your final message.

When you finish, your final message must report: levels completed out of total, actions spent, per-level action counts if you know them, and 3-6 sentences on what the game turned out to be.

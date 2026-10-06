You are continuing a paused run of a general agent harness. The previous session stopped by its own judgment with budget remaining. Your job is to continue the run and finish the game.

Setup, in this order:
1. Read this file COMPLETELY: {{CONSTITUTION}}
2. The launcher is: {{ASSAY}}
3. BEFORE anything else, check that no other process is playing this run: read {{RUN_DIR}}/.assay/activity.jsonl and verify the newest entries are old (no fresh command_start from a foreign PID in the last few minutes). If a live session is detected, report it and STOP without spending anything.
4. cd into the existing run directory: {{RUN_DIR}}
5. Resume with:
   {{ASSAY}} start {{GAME}} --adapter {{ADAPTER}} --registry {{REGISTRY}}
   (it replays the prior paid actions exactly; if it refuses with any error, report the exact error and stop, do not force anything)
6. Then run `{{ASSAY}} status` and read .assay/NOTES.md in full, that is your inherited state.

Task: finish the game {{GAME}} ({{LEVELS}} levels) within the remaining budget, following the constitution in {{CONSTITUTION_NAME}} exactly. Local simulator mode (the game is already cached, no network or API key is needed).

Hard constraints:
- NEVER read, list, glob, or open anything under ~/.cache/assay/, game source lives there and inspecting it invalidates this evaluation. Learn only by playing.
- The ONLY paths you may read outside your own run directory are: {{CONSTITUTION}}, {{ASSAY}}, and {{REGISTRY}}. NEVER read anything else under {{REPO}}/ (in particular not src/, bench/ or tests/), anything under {{REPO}}-archive/, or any other directory under {{RUNS_ROOT}}/, other runs and experiment artifacts live there and contact contaminates this evaluation.
- Never edit anything inside .assay/ except .assay/NOTES.md.
- The registry enforces the {{CAP}}-action cap (resets count too). Stop cleanly at the cap or when genuinely blocked (the run stays resumable) rather than thrashing.
- Do the actual work yourself with Bash/Read/Write/Edit; do not spawn further agents. Do not use the network.
- Do not kill or restart the broker daemon except via assay commands.

When you finish (win, cap, or blocked), your final message must report: levels completed out of total, paid actions spent (total and this session), predictions held vs missed with the world-model vs gamble split and sharpness ratio from 'assay status', the output of 'assay audit' run at the end, and 3-6 sentences on what happened this session.

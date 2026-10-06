You are evaluating a general agent harness by actually playing a game with it.

Setup, in this order:
1. Read this file COMPLETELY before doing anything else: {{CONSTITUTION}}
2. The launcher is: {{ASSAY}}
3. Your run directory (cd into it, one directory = one run): {{RUN_DIR}}
4. Start the run with:
   {{ASSAY}} start {{GAME}} --adapter {{ADAPTER}} --registry {{REGISTRY}}
   (if the directory already owns this run, the same command resumes it exactly: run `{{ASSAY}} status` and read .assay/NOTES.md in full, that is your inherited state)

Task: solve the game {{GAME}}, following the constitution in {{CONSTITUTION_NAME}} exactly. Local simulator mode (the default; the game is already cached, no network or API key is needed). The registry declares the action names, parameter schemas, and untrusted description hints; the status output describes the interface. The observation is a 64x64 color grid rendered for you as an image and printable views. The environment reports its own level counter and win state. This game has {{LEVELS}} levels.

Hard constraints:
- NEVER read, list, glob, or open anything under ~/.cache/assay/, game source lives there and inspecting it invalidates this evaluation. Learn only by playing.
- The ONLY paths you may read outside your own run directory are: {{CONSTITUTION}}, {{ASSAY}}, and {{REGISTRY}}. NEVER read anything else under {{REPO}}/ (in particular not src/, bench/ or tests/), anything under {{REPO}}-archive/, or any other directory under {{RUNS_ROOT}}/, other runs and experiment artifacts live there and contact contaminates this evaluation.
- Never edit anything inside .assay/ except .assay/NOTES.md.
- Budget: the registry enforces a hard cap of {{CAP}} paid actions (resets count too). If you hit the cap or judge the game unfinishable, stop cleanly (the run stays resumable) rather than thrashing.
- Do the actual work yourself with Bash/Read/Write/Edit; do not spawn further agents. Do not use the network.
- Do not kill or restart the broker daemon except via assay commands.

When you finish (win, cap, or blocked), your final message must report: levels completed out of total, paid actions spent, per-level action counts, the meter lines of 'assay status' at the end (everything before the NOTES block), how many channels you declared, any GAME_OVER events and what caused them, the output of 'assay audit' run at the end, and 3-6 sentences on what the game turned out to be and how the harness shaped your play.

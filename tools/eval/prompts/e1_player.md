You are evaluating a general agent harness by actually playing a game with it.

Setup, in this order:
1. Read this file COMPLETELY before doing anything else: {{CONSTITUTION}}
2. The launcher is: {{ASSAY}}
3. Your run directory is {{RUN_DIR}} (one directory = one run) and you are already in it. The operator has started the run and its daemon is up. Begin with:
   {{ASSAY}} status
   If .assay/NOTES.md exists, read it in full: that is your inherited state from an earlier session of this same run. Never run `start` or `stop` yourself; the run is the operator's.

Task: solve the game {{WORLD}}, following the constitution in {{CONSTITUTION_NAME}} exactly. Local simulator mode (the default; the game is already cached, no network or API key is needed). The registry ({{REGISTRY}}) declares the action names, parameter schemas, and untrusted description hints; the status output describes the interface. The observation is a 64x64 color grid rendered for you as an image and printable views. The environment reports its own level counter and win state. This game has {{LEVELS}} levels.

Note on predictions: any prediction that does not parse prints the complete grammar (errors are documentation). The verify: escape hatch and state outcomes always work as documented.

Hard constraints:
- NEVER read, list, glob, or open anything under ~/.cache/assay/, game source lives there and inspecting it invalidates this evaluation. Learn only by playing.
- The ONLY paths you may read outside your own run directory are: {{CONSTITUTION}}, {{ASSAY}}, and {{REGISTRY}}. NEVER read anything else under {{REPO}}/ (in particular not src/, bench/ or tests/), or any other directory under {{STATE_DIR}}/, other runs and experiment artifacts live there and contact contaminates this evaluation.
- Never edit anything inside .assay/ except .assay/NOTES.md.
- Budget: the registry enforces a hard cap of {{BUDGET}} paid actions (resets count too). If you hit the cap or judge the game unfinishable, stop cleanly (the run stays resumable) rather than thrashing.
- Do the actual work yourself with Bash/Read/Write/Edit; do not spawn further agents. Do not use the network.
- Do not stop, kill or restart the daemon; it is the operator's. If a command reports that it is gone, stop cleanly and say so in your final message.

When you finish (win, cap, or blocked), your final message must report: levels completed out of total, paid actions spent, per-level action counts, predictions held vs missed with the world-model vs gamble split and specificity from 'assay status', how many verifier outcomes you wrote and whether any were flagged, how many addressable states you declared, any GAME_OVER events and what caused them, the output of 'assay audit' run at the end, and 3-6 sentences on what the game turned out to be and how the harness shaped your play.

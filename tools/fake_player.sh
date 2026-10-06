#!/usr/bin/env bash
# fake_player.sh: stand-in for run_player.sh in orchestrator tests. Same
# interface (PROMPT RUN_DIR LEDGER [--max-hours H]); exits immediately.
# FAKE_PLAYER_MODE: win (journal reaches WIN), stall (journal stays NOT_FINISHED
# at 3 paid actions), ratelimit-once (first call per run dir reports a usage
# limit and exits 1, later calls behave like win).
set -euo pipefail
PROMPT="$1"; RUN_DIR="$2"; LEDGER="$3"
MODE="${FAKE_PLAYER_MODE:-win}"
mkdir -p "$RUN_DIR/.assay"
MARK="$RUN_DIR/.fake_calls"
CALLS=$(( $(cat "$MARK" 2>/dev/null || echo 0) + 1 )); echo "$CALLS" > "$MARK"
if [[ "$MODE" == "ratelimit-once" && "$CALLS" -eq 1 ]]; then
  RESULT="You've hit your usage limit. Rate limit reached, resets at 6pm"; EXIT=1; STATE="NOT_FINISHED"; PAID=0
elif [[ "$MODE" == "stall" ]]; then
  RESULT="stopped"; EXIT=0; STATE="NOT_FINISHED"; PAID=3
else
  RESULT="RESULT: state=WIN"; EXIT=0; STATE="WIN"; PAID=5
fi
if [[ "$PAID" -gt 0 ]]; then
  : > "$RUN_DIR/.assay/events.jsonl"
  echo '{"id":0,"action":"START","counts_action":false,"state":"NOT_FINISHED","levels_completed":0,"win_levels":1}' >> "$RUN_DIR/.assay/events.jsonl"
  for i in $(seq 1 "$PAID"); do
    S="NOT_FINISHED"; [[ "$i" -eq "$PAID" ]] && S="$STATE"
    echo "{\"id\":$i,\"action\":\"NOOP\",\"counts_action\":true,\"state\":\"$S\",\"levels_completed\":0,\"win_levels\":1}" >> "$RUN_DIR/.assay/events.jsonl"
  done
fi
python3 - "$LEDGER" "$RESULT" "$EXIT" "$PROMPT" <<'PY'
import json, sys, pathlib, datetime
ledger, result, code, prompt = sys.argv[1:]
path = pathlib.Path(ledger); existing = json.loads(path.read_text()) if path.exists() else []
existing.append({"prompt_file": prompt, "model_requested": "fake", "model_reported": "fake-model",
                 "started": datetime.datetime.now(datetime.timezone.utc).isoformat(), "ended": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 "exit_code": int(code), "is_error": int(code) != 0, "num_turns": 1, "total_cost_usd": 0.01,
                 "usage": {"input_tokens": 1, "output_tokens": 1}, "result_text": result, "stderr_tail": "",
                 "claude_json": {"result": result, "is_error": int(code) != 0}})
path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(existing, indent=2))
PY
exit "$EXIT"

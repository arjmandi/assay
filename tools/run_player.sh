#!/usr/bin/env bash
# run_player.sh: launch ONE unattended ASSAY player session with `claude -p` and
# write a per-run ledger.
#
#   tools/run_player.sh PROMPT_FILE RUN_DIR LEDGER_JSON [--model ID] [--max-hours H]
#
# The prompt file is sent verbatim as the single user turn (render a template
# first, see tools/prompts/ and tools/render_prompt.py). The player runs with
# cwd = RUN_DIR; Bash, Read, Glob, Grep are allowed, Write/Edit only under
# RUN_DIR. Everything else is denied: no network tools, no agents.
#
# Environment handled here, not in the kernel:
#   ASSAY_PYTHON_BIN  bin dir of a Python that serves the kernel AND imports
#                     arc_agi (numpy 2, pillow 10-12). It is prepended to PATH so
#                     bin/assay's `python3` resolves to it. Default: the venv the
#                     campaign served the broker from, PRO-LONG/.venv (Python 3.14;
#                     the launcher's managed venv never gets arc_agi). The
#                     interpreter path and package versions go into the ledger.
#   ASSAY_ANCHOR_DIR  passed through when set (E2 resume states REQUIRE it).
#   CLAUDE_MODEL      default model id (claude-opus-5).
#
# Billing: claude -p is subscription-billed when ANTHROPIC_API_KEY is unset and
# the CLI is logged in. This script does not set or unset credentials. It records
# whether ANTHROPIC_API_KEY was present in the ledger (never its value).
#
# Ledger (JSON): prompt sha256, run dir, model requested and as reported, start
# and end timestamps (UTC), wall seconds, exit code, the full claude -p JSON
# result (usage, total_cost_usd at list price, modelUsage, num_turns, ...) and the
# journal summary at exit (events, paid actions, state, levels).

set -uo pipefail

usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[[ $# -lt 3 ]] && usage

PROMPT_FILE="$1"; RUN_DIR="$2"; LEDGER="$3"; shift 3
MODEL="${CLAUDE_MODEL:-claude-opus-5}"
MAX_HOURS="${MAX_HOURS:-4}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --max-hours) MAX_HOURS="$2"; shift 2 ;;
    *) echo "unknown argument $1" >&2; usage ;;
  esac
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY_BIN="${ASSAY_PYTHON_BIN:-/Users/mohsenarjmandi/workspace/PRO-LONG/.venv/bin}"
if ! "$PY_BIN/python3" -c 'import sys; assert sys.version_info >= (3, 12); import numpy, PIL, arc_agi' 2>/dev/null; then
  echo "ASSAY_PYTHON_BIN=$PY_BIN does not serve the kernel with arc_agi" >&2; exit 3
fi
export PATH="$PY_BIN:$PATH"
export _ZO_DOCTOR=0

mkdir -p "$RUN_DIR" "$(dirname "$LEDGER")"
RUN_DIR="$(cd "$RUN_DIR" && pwd)"
PROMPT="$(cat "$PROMPT_FILE")"
PROMPT_SHA="$(shasum -a 256 "$PROMPT_FILE" | cut -d' ' -f1)"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_S=$(date +%s)
OUT="$(mktemp "${TMPDIR:-/tmp}/assay-player-XXXXXX")"   # no suffix: macOS mktemp replaces only trailing Xs
ERR="${OUT}.err"
TIMEOUT_S=$(( MAX_HOURS * 3600 ))

# Tools: Bash, Read, Glob and Grep anywhere (the prompt confines reads to the
# constitution, launcher and registry); Write and Edit only inside the run
# directory (path-scoped rules, resolved against cwd = RUN_DIR). No --add-dir, so
# the repo is never writable from the player's tool permissions. Everything not
# listed is denied in -p mode (no prompt can be answered unattended).
ALLOWED=(
  "Bash" "Read" "Glob" "Grep" "Write(./**)" "Edit(./**)" "MultiEdit(./**)"
)
DISALLOWED=(
  "WebFetch" "WebSearch" "Agent" "Task" "NotebookEdit" "TodoWrite" "KillShell"
)

cd "$RUN_DIR" || exit 3
API_KEY_PRESENT=$([[ -n "${ANTHROPIC_API_KEY:-}" ]] && echo true || echo false)
PY_VERSIONS="$("$PY_BIN/python3" -c 'import sys, importlib.metadata as m; print(sys.executable, sys.version.split()[0], *(f"{d}={m.version(d)}" for d in ("arc-agi","arcengine","numpy","pillow")))' 2>/dev/null)"
REPO_COMMIT="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
REPO_BRANCH="$(git -C "$REPO" branch --show-current 2>/dev/null || echo unknown)"

# perl alarm gives a portable wall-clock cap (coreutils timeout is not on macOS by default)
perl -e 'alarm shift; exec @ARGV' "$TIMEOUT_S" \
  claude --model "$MODEL" -p "$PROMPT" \
    --output-format json \
    --permission-mode acceptEdits \
    --allowedTools "${ALLOWED[@]}" \
    --disallowedTools "${DISALLOWED[@]}" \
    > "$OUT" 2> "$ERR"
EXIT=$?
ENDED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
END_S=$(date +%s)

"$PY_BIN/python3" - "$OUT" "$ERR" "$LEDGER" "$RUN_DIR" "$PROMPT_FILE" "$PROMPT_SHA" "$MODEL" "$STARTED" "$ENDED" "$((END_S - START_S))" "$EXIT" "$API_KEY_PRESENT" "$TIMEOUT_S" "$PY_VERSIONS" "$REPO_COMMIT" "$REPO_BRANCH" "${ASSAY_ANCHOR_DIR:-}" <<'PY'
import json, sys, pathlib
out, err, ledger, run_dir, prompt_file, prompt_sha, model, started, ended, wall, code, key_present, timeout_s, py_versions, repo_commit, repo_branch, anchor_dir = sys.argv[1:]
raw = pathlib.Path(out).read_text()
try:
    result = json.loads(raw)
except json.JSONDecodeError:
    result = {"unparsed_stdout": raw[-4000:]}
stderr = pathlib.Path(err).read_text()[-4000:]
journal = {}
events = pathlib.Path(run_dir) / ".assay" / "events.jsonl"
if events.exists():
    lines = [l for l in events.read_text().splitlines() if l.strip()]
    if lines:
        last = json.loads(lines[-1])
        journal = {"events": len(lines),
                   "paid": sum(1 for l in lines if json.loads(l).get("counts_action")),
                   "state": last.get("state"), "levels_completed": last.get("levels_completed"),
                   "win_levels": last.get("win_levels")}
usage = result.get("modelUsage") or {}
reported_model = next(iter(usage), None)
record = {
    "prompt_file": prompt_file, "prompt_sha256": prompt_sha, "run_dir": run_dir,
    "model_requested": model, "model_reported": reported_model,
    "canonical_model": (usage.get(reported_model) or {}).get("canonicalModel") if reported_model else None,
    "provider": (usage.get(reported_model) or {}).get("provider") if reported_model else None,
    "cost_basis": (usage.get(reported_model) or {}).get("costBasis") if reported_model else None,
    "anthropic_api_key_present": key_present == "true",
    "broker_interpreter": py_versions, "assay_commit": repo_commit, "assay_branch": repo_branch,
    "assay_anchor_dir": anchor_dir or None,
    "started": started, "ended": ended, "wall_seconds": int(wall), "wall_limit_seconds": int(timeout_s),
    "exit_code": int(code), "timed_out": int(code) == 142,
    "total_cost_usd": result.get("total_cost_usd"), "usage": result.get("usage"),
    "num_turns": result.get("num_turns"), "session_id": result.get("session_id"),
    "is_error": result.get("is_error"), "subtype": result.get("subtype"),
    "result_text": (result.get("result") or "")[-6000:] if isinstance(result.get("result"), str) else result.get("result"),
    "journal_at_exit": journal, "stderr_tail": stderr, "claude_json": result,
}
existing = []
path = pathlib.Path(ledger)
if path.exists():
    try:
        existing = json.loads(path.read_text())
        if not isinstance(existing, list):
            existing = [existing]
    except json.JSONDecodeError:
        existing = []
existing.append(record)
path.write_text(json.dumps(existing, indent=2) + "\n")
print(json.dumps({k: record[k] for k in ("model_reported", "started", "ended", "wall_seconds", "exit_code", "total_cost_usd", "num_turns", "journal_at_exit")}))
PY
rm -f "$OUT" "$ERR"
exit $EXIT

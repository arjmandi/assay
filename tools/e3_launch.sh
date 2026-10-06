#!/usr/bin/env bash
# e3_launch.sh: start the E3 queue, but only after both import runs are prepared.
#
#   tools/e3_launch.sh [--dry-run] [other night_orchestrator.py flags]
#
# The orchestrator has no pre-step, so the import runs must exist before it
# launches a player (a player in an empty directory would run a COLD gated game
# and mislabel it E3). This script refuses unless `e3_prepare.py GAME --check`
# passes for ft09 and tr87, refuses while the E1/E2 queue flag exists, and then
# execs the orchestrator with E3's own jobs file, state dir and running flag so
# nothing collides with the E1/E2 ledgers, status.json or STOP file.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ASSAY_PYTHON_BIN:-/Users/mohsenarjmandi/workspace/PRO-LONG/.venv/bin}/python3"
STATE_DIR="/Users/mohsenarjmandi/workspace/assay-runs/e3/orchestrator"
FLAG="/Users/mohsenarjmandi/workspace/assay-archive/runs/E3_RUNNING"
E1E2_FLAG="/Users/mohsenarjmandi/workspace/assay-archive/runs/E1_E2_RUNNING"
export _ZO_DOCTOR=0

for game in ft09 tr87; do
  "$PY" "$REPO/tools/e3_prepare.py" "$game" --check
done
if [[ -e "$E1E2_FLAG" ]]; then
  echo "E3 LAUNCH | refused: $E1E2_FLAG exists, the E1/E2 queue is still running" >&2
  exit 2
fi
exec "$PY" "$REPO/tools/night_orchestrator.py" \
  --jobs "$REPO/tools/jobs_e3.json" \
  --state-dir "$STATE_DIR" \
  --running-flag "$FLAG" \
  --max-concurrency 2 "$@"

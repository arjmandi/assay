#!/usr/bin/env bash
# The kernel take-home experiment, one step per subcommand. RUNBOOK.md says when
# to run each. Nothing here starts on its own: every step is a command the
# operator types.
#
#   kernel_exp.sh setup                     fetch the task and the wrapper, build both
#   kernel_exp.sh smoke                     the pack's smoke test: one evaluation of the untouched kernel
#   kernel_exp.sh host-test MODEL           the counter world to a WIN through OpenCode with MODEL
#   kernel_exp.sh assay ARM SEED [HOURS]    start an ASSAY arm (A1, A2, A3) and play it
#   kernel_exp.sh resume ARM SEED [HOURS]   the session runner again on a run whose daemon is up
#   kernel_exp.sh baseline ARM SEED [HOURS] a wrapper baseline (OE-G, OE-D, SH-G, SH-D)
#   kernel_exp.sh rescore FILE              the clean re-score of one program
#   kernel_exp.sh frontier ARM SEED         the frontier summary of an ASSAY run
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
PACK="$REPO/worlds/recipes/kernel-takehome"
WRAPPER="$PACK/upstream/rsi-top-wrapper"
ASSAY_BIN="$REPO/bin/assay"

[ -f "$HERE/arms.env" ] || { echo "ERROR | copy arms.env.example to arms.env first" >&2; exit 2; }
# shellcheck disable=SC1091
. "$HERE/arms.env"
RUNS="$KERNEL_EXP/runs"
BASELINES="$KERNEL_EXP/baselines"
TOKENS="$HOME/assay/tokens"

fail() { echo "ERROR | $*" >&2; exit 2; }
need() { [ -n "${!1:-}" ] || fail "$1 is not set in this shell"; }

player_of() {   # ARM: host, model, analyst host, analyst model
  case "$1" in
    A1) echo "opencode $OPENCODE_GEMINI_MODEL opencode $OPENCODE_DEEPSEEK_MODEL" ;;
    A2) echo "opencode $OPENCODE_DEEPSEEK_MODEL opencode $OPENCODE_DEEPSEEK_MODEL" ;;
    A3) echo "claude $CLAUDE_OPUS_MODEL claude $CLAUDE_OPUS_MODEL" ;;
    *) fail "$1 is not an ASSAY arm (A1, A2, A3)" ;;
  esac
}

session() {     # ARM SEED HOURS
  local arm="$1" seed="$2" hours="$3" run host model analyst analyst_model
  run="$RUNS/$arm-s$seed"
  read -r host model analyst analyst_model <<<"$(player_of "$arm")"
  [ -d "$run/.assay" ] || fail "$run holds no started run"
  cd "$REPO"
  python3 -m tools.eval session --run-dir "$run" --player "$host" --model "$model" \
    --hours "$hours" --max-sessions "$MAX_SESSIONS" --max-usd "$BUDGET_USD" \
    --stall --analyst "$analyst" --analyst-model "$analyst_model" \
    --analyst-context "$run/work/Readme.md" --analyst-context "$run/.assay/world_state/best.py" \
    --analyst-context "$run/work/perf_takehome.py" \
    2>&1 | tee -a "$run.sessions.log"
}

filled() { case "$1" in *FILL_IN*) fail "fill in the Gemini 3.7 Flash id in arms.env (CHECKS.md, Open)" ;; esac; }

# The experiment is run on ASSAY 1.5.1 and nothing else: the tagged kernel, hooks,
# launcher, manual and world engine, with this experiment's additions on top.
ASSAY_TAG="v1.5.1"
pinned() {
  local said
  said="$(cd "$REPO" && python3 -c 'import sys; sys.path.insert(0, "src"); import assay; print(assay.__version__)')"
  [ "$said" = "${ASSAY_TAG#v}" ] || fail "this checkout is ASSAY $said, not ${ASSAY_TAG#v}"
  git -C "$REPO" rev-parse -q --verify "refs/tags/$ASSAY_TAG" >/dev/null || fail "the tag $ASSAY_TAG is not in this checkout"
  git -C "$REPO" diff --quiet "refs/tags/$ASSAY_TAG" -- src hooks bin CONSTITUTION.md worlds/archetypes pyproject.toml \
    || fail "src, hooks, bin, CONSTITUTION.md, worlds/archetypes or pyproject.toml differ from $ASSAY_TAG"
}

command="${1:-}"; shift || true
case "$command" in setup|smoke|host-test|assay|resume) pinned ;; esac
case "$command" in
  setup)
    python3 "$PACK/prepare.py"
    (cd "$WRAPPER" && ./setup.sh)
    mkdir -p "$RUNS" "$BASELINES"; mkdir -p -m 700 "$TOKENS"
    echo "SETUP | the task and the wrapper are in $PACK/upstream; runs go to $KERNEL_EXP"
    ;;
  smoke)
    scratch="$(mktemp -d)"; mkdir "$scratch/run"
    "$ASSAY_BIN" --run-dir "$scratch/run" start smoke --adapter "$PACK/adapter.py:factory" \
      --registry "$PACK/registry.json" --owner-token-file "$scratch/smoke.token"
    "$ASSAY_BIN" --run-dir "$scratch/run" doctor
    "$ASSAY_BIN" --run-dir "$scratch/run" state declare cycles --path cycles
    "$ASSAY_BIN" --run-dir "$scratch/run" act EVALUATE label=untouched --predict "cycles = 147734"
    "$ASSAY_BIN" --run-dir "$scratch/run" audit
    "$ASSAY_BIN" --run-dir "$scratch/run" stop
    cat "$scratch/run/.assay/world_state/frontier.jsonl"
    rm -rf "$scratch"
    ;;
  host-test)
    model="${1:?a model id as OpenCode names it}"
    scratch="$KERNEL_EXP/host-test/$(echo "$model" | tr '/:' '__')"
    rm -rf "$scratch" "$scratch.sessions"; mkdir -p "$scratch"
    "$ASSAY_BIN" --run-dir "$scratch" start counter --adapter "$REPO/examples/counter_world.py:factory" \
      --registry "$REPO/examples/example_registry.json" --owner-token-file "$scratch.token"
    sed -e "s|{{CONSTITUTION}}|$REPO/CONSTITUTION.md|" "$HERE/counter_player.md" > "$scratch/PLAYER.md"
    cd "$REPO"
    python3 -m tools.eval session --run-dir "$scratch" --player opencode --model "$model" \
      --hours 1 --max-sessions 3 --max-usd 5
    echo "HOST TEST | read $scratch.sessions/sessions.json: the outcome, the host version, and whether"
    echo "HOST TEST | dollars and tokens were found in s1.player.out (RUNBOOK.md, step 2)"
    ;;
  assay)
    arm="${1:?an arm}"; seed="${2:?a seed}"; hours="${3:-$HOURS}"
    run="$RUNS/$arm-s$seed"
    [ -e "$run" ] && fail "$run exists; one directory is one run (use resume, or another seed)"
    [ -d "$PACK/task" ] || fail "run setup first"
    read -r host model _ _ <<<"$(player_of "$arm")"
    filled "$model"
    mkdir -p "$run"
    "$ASSAY_BIN" --run-dir "$run" start "kernel-$arm-s$seed" --adapter "$PACK/adapter.py:factory" \
      --registry "$PACK/registry.json" --seed "$seed" --owner-token-file "$TOKENS/$arm-s$seed.token"
    if [ "$host" = claude ]; then
      ASSAY="$ASSAY_BIN" "$ASSAY_BIN" --run-dir "$run" hooks install --policy "$TOKENS/$arm-s$seed.hooks.json" \
        --owner-token-file "$TOKENS/$arm-s$seed.token" --deny 'eval_build' --deny 'eval_score' \
        --deny 'submission_tests' --deny 'world_state' --deny 'kernel_world[.]py' --deny 'worlds/recipes'
    fi
    budget="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["budget"]["actions"])' "$PACK/registry.json")"
    sed -e "s|{{CONSTITUTION}}|$REPO/CONSTITUTION.md|" -e "s|{{BUDGET}}|$budget|" "$HERE/player.md" > "$run/PLAYER.md"
    session "$arm" "$seed" "$hours"
    ;;
  resume)
    session "${1:?an arm}" "${2:?a seed}" "${3:-$HOURS}"
    ;;
  baseline)
    arm="${1:?an arm}"; seed="${2:?a seed}"; hours="${3:-$HOURS}"
    case "$arm" in
      OE-G) framework=openevolve; provider=gemini; model="$WRAPPER_GEMINI_MODEL"; need GEMINI_API_KEY ;;
      OE-D) framework=openevolve; provider=deepseek; model="$WRAPPER_DEEPSEEK_MODEL"; need DEEPSEEK_API_KEY ;;
      SH-G) framework=shinka; provider=gemini; model="$WRAPPER_GEMINI_MODEL"; need GEMINI_API_KEY; need OPENROUTER_API_KEY ;;
      SH-D) framework=shinka; provider=deepseek; model="$WRAPPER_DEEPSEEK_MODEL"; need DEEPSEEK_API_KEY; need OPENROUTER_API_KEY ;;
      *) fail "$arm is not a baseline arm (OE-G, OE-D, SH-G, SH-D)" ;;
    esac
    filled "$model"
    out="$BASELINES/$arm-s$seed"
    [ -e "$out" ] && fail "$out exists"
    cd "$WRAPPER"
    uv run python run.py baseline --framework "$framework" --provider "$provider" --model "$model" \
      --hours "$hours" --budget "$BUDGET_USD" --seed "$seed" --output "$out" 2>&1 | tee "$out.log"
    ;;
  rescore)
    python3 "$PACK/checks/score.py" "${1:?a program file}" --json
    ;;
  frontier)
    cd "$REPO"
    python3 -m tools.eval frontier --run-dir "$RUNS/${1:?an arm}-s${2:?a seed}"
    ;;
  *)
    sed -n '2,14p' "$0"; exit 2 ;;
esac

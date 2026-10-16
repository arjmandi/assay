"""The compare mode: one pack, two arms, the same model, the same dollars and
hours per run, and the same external checker.

    python3 -m tools.eval compare --pack DIR [--seeds 1 2 3] [--model M]
        [--max-usd USD] [--hours H] [--max-sessions N] [--arms assay,goal]
        [--state-dir DIR] [--assay CMD] [--dry-run]
        [--goal-player CMD] [--assay-player CMD] [--assay-prompt FILE]

The pack's `compare.json` is the design, written before any run: the world,
the seeds, the model, the budgets, the hypothesis, the metric, how the result
is read, the `/goal` condition, how the baseline reaches the world (and its
own wording of the task, when the registry's speaks of ASSAY), and the checker
for each arm. `/assay:compare` runs this mode.

- **The assay arm** is what `/assay:run` does. Per seed the mode starts a run
  as an operator would (`assay start` with the pack's adapter, registry and
  seed, the owner token outside the run, the hooks with the design's deny
  patterns), writes the player's prompt, plays it through the session mode
  (relaunches, the Stop gate, the world's secrets kept from the player),
  writes the report with the checker (report.py, `--check`), and reads the
  verdict from it.
- **The goal arm** is Claude Code with `/goal` and no ASSAY. Per seed the mode
  builds a bare copy of the world in a directory of its own: the copy mode's
  files under `world/`, or the worktree mode's tree under `work/`, built by
  the recipe's own setup, and `world.py`, which runs the recipe's own observe
  and act commands, so the world answers and refuses as it does under ASSAY,
  with no prediction, no gate and no journal. It launches one `claude -p
  "/goal <condition> ..."` session there, reads the stream for how it ended,
  and runs the checker on the directory. In the copy mode it then replays the
  logged actions on a fresh copy, so a copy changed by hand rather than
  through `world.py` is named.

Both arms run with the same model, no MCP server, the same tool permissions
(acceptEdits, Bash, reads, writes inside the working directory, no web, no
subagents), `--max-usd` per run and `--hours` per run. The ASSAY arm may span
`--max-sessions` sessions inside those; the goal arm is one session, which
`/goal` keeps going until its evaluator judges the condition met or Claude
Code's cap on consecutive blocked stops ends it.

A run claims the goal when it ends at WIN or a declared stop (the assay arm)
or when `/goal` judged its condition met (the goal arm). A false completion is
a claim the checker does not score correct. The state directory (default
`./compare-state/<pack>`) holds `PREREGISTRATION.json` and `.md`, written
before the first launch and refused if the design changes later, the runs,
the logs, `results.jsonl` (one row per arm and seed) and `COMPARE.md`. A row
already there is not run again.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import EvalError
from .report import build as build_report
from .report import write as write_report
from .runner import DEFAULT_LAUNCHER, REPO, now

PROMPTS = REPO / "tools" / "eval" / "prompts"
CONSTITUTION = REPO / "CONSTITUTION.md"
ARMS = ("assay", "goal")
ALLOWED_TOOLS = ("Bash", "Read", "Glob", "Grep", "Write(./**)", "Edit(./**)")
DISALLOWED_TOOLS = ("WebFetch", "WebSearch", "Agent")
CLAIMED = {"assay": ("WIN", "SELF_STOP"), "goal": ("met",)}
REQUIRED = ("world", "seeds", "model", "max_usd", "hours", "hypothesis", "metric", "reading", "goal", "access", "check")
BLOCK_CAP_KEY = "stop-hook-block-cap"
WORLD_SCRIPT = '''#!/usr/bin/env python3
"""The world of this directory, without a referee: the recipe's own commands.

    python3 world.py observe
    python3 world.py act ACTION '{{"param": "value"}}'

act prints the world's result and then what it observes. Every act is
logged for the comparison, outside this directory."""

import json
import subprocess
import sys

CONFIG = json.loads({config!r})


def run(argv, stdin=None):
    done = subprocess.run(argv, cwd=CONFIG["workdir"], input=stdin, capture_output=True, text=True,
                          timeout=CONFIG["timeout"])
    if done.returncode != 0:
        sys.exit("the world's command failed: " + (done.stderr.strip() or done.stdout.strip())[-2000:])
    return done.stdout.strip()


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "observe":
        print(run(CONFIG["observe"]))
        return 0
    if len(sys.argv) in (3, 4) and sys.argv[1] == "act":
        action = sys.argv[2]
        if action not in CONFIG["actions"]:
            sys.exit("the actions are " + ", ".join(CONFIG["actions"]))
        params = json.loads(sys.argv[3]) if len(sys.argv) == 4 else {{}}
        if not isinstance(params, dict):
            sys.exit("the parameters are one JSON object")
        result = run([action if word == "{{action}}" else word for word in CONFIG["act"]], json.dumps(params))
        with open(CONFIG["log"], "a") as log:
            log.write(json.dumps({{"action": action, "params": params}}) + "\\n")
        print(result)
        print(run(CONFIG["observe"]))
        return 0
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main())
'''


@dataclasses.dataclass(frozen=True)
class Design:
    pack: Path
    raw: dict[str, Any]
    recipe: dict[str, Any]
    registry: dict[str, Any]

    @property
    def name(self) -> str:
        return self.pack.name

    @property
    def mode(self) -> str:
        return str(self.recipe["world"].get("mode", "copy"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_design(pack: Path) -> Design:
    pack = pack.resolve()
    path = pack / "compare.json"
    if not path.is_file():
        raise EvalError(f"no compare.json in {pack}: a pack names its comparison there")
    try:
        raw = json.loads(path.read_text())
        recipe = json.loads((pack / "recipe.json").read_text())
        registry = json.loads((pack / "registry.json").read_text())
    except (OSError, ValueError) as error:
        raise EvalError(f"{pack}: {error}") from None
    if not isinstance(raw, dict) or raw.get("compare") != 1:
        raise EvalError(f"{path}: not a comparison design (\"compare\": 1)")
    missing = [key for key in REQUIRED if key not in raw]
    if missing:
        raise EvalError(f"{path}: missing {', '.join(missing)}")
    if recipe.get("world", {}).get("mode", "copy") not in ("copy", "worktree"):
        raise EvalError(f"{pack}: the goal arm builds command worlds only (copy or worktree), not a service")
    for arm in ARMS:
        if not isinstance(raw["check"].get(arm), list):
            raise EvalError(f"{path}: check.{arm} must be an argv list")
    return Design(pack=pack, raw=raw, recipe=recipe, registry=registry)


def fill(template: str, values: dict[str, str]) -> str:
    text = template
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    if "{{" in text:
        raise EvalError(f"an unfilled placeholder in a prompt: {text[text.index('{{'):][:40]}")
    return text


def _words(template: list[str], values: dict[str, str]) -> list[str]:
    out = []
    for word in template:
        for key, value in values.items():
            word = word.replace("{" + key + "}", value)
        out.append(word)
    return out


def goal_ending(stdout: str, *, timed_out: bool) -> dict[str, Any]:
    """How a `claude -p "/goal ..."` session ended, from its stream: `met`
    (the session ended without its last stop blocked, so the evaluator judged
    the condition met), `cap` (Claude Code ended it after too many blocked
    stops), `budget`, `time`, or `error`; with the cost, the turns and the
    number of times the evaluator sent the player back."""
    result: dict[str, Any] | None = None
    capped = False
    feedback = 0
    for line in stdout.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        kind = record.get("type")
        if kind == "result":
            result = record
        elif kind == "system" and record.get("subtype") == "notification" and record.get("key") == BLOCK_CAP_KEY:
            capped = True
        elif kind == "user" and "Stop hook feedback" in json.dumps(record.get("message")):
            feedback += 1
    subtype = str((result or {}).get("subtype") or "")
    if timed_out:
        ended = "time"
    elif result is None:
        ended = "error"
    elif "budget" in subtype:
        ended = "budget"
    elif capped:
        ended = "cap"
    elif subtype == "success" and not result.get("is_error"):
        ended = "met"
    else:
        ended = "error"
    found = (result or {}).get("usage")
    usage: dict[str, Any] = found if isinstance(found, dict) else {}
    tokens = 0
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        tokens += int(usage.get(key) or 0)
    cost = (result or {}).get("total_cost_usd")
    return {
        "ended_by": ended,
        "dollars": float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
        "tokens": tokens or None,
        "turns": (result or {}).get("num_turns"),
        "goal_feedback": feedback,
        "result": str((result or {}).get("result") or "")[-2000:],
    }


class Compare:
    def __init__(
        self, design: Design, *, state_dir: Path, seeds: list[int], model: str, max_usd: float, hours: float,
        max_sessions: int, arms: list[str], launcher: list[str], goal_player: list[str] | None = None,
        assay_player: list[str] | None = None, assay_prompt: Path | None = None,
    ) -> None:
        self.design = design
        self.state = state_dir.resolve()
        self.seeds = seeds
        self.model = model
        self.max_usd = max_usd
        self.hours = hours
        self.max_sessions = max_sessions
        self.arms = arms
        self.launcher = launcher
        self.goal_player = goal_player
        self.assay_player = assay_player
        self.assay_prompt = assay_prompt

    # ---- the pre-registration -------------------------------------------------------
    def plan(self) -> dict[str, Any]:
        raw = self.design.raw
        return {
            "compare": 1,
            "pack": self.design.name,
            "world": raw["world"],
            "design_sha256": _sha(self.design.pack / "compare.json"),
            "recipe_sha256": _sha(self.design.pack / "recipe.json"),
            "registry_sha256": _sha(self.design.pack / "registry.json"),
            "prompts_sha256": {name: _sha(PROMPTS / name) for name in ("compare_assay.md", "compare_goal.md")},
            "arms": self.arms,
            "seeds": self.seeds,
            "model": self.model,
            "max_usd_per_run": self.max_usd,
            "hours_per_run": self.hours,
            "max_sessions": self.max_sessions,
            "players": {"assay": self.assay_player or "claude", "goal": self.goal_player or "claude"},
            "hypothesis": raw["hypothesis"],
            "metric": raw["metric"],
            "reading": raw["reading"],
        }

    def preregister(self) -> None:
        path = self.state / "PREREGISTRATION.json"
        plan = self.plan()
        if path.is_file():
            held = json.loads(path.read_text())
            differs = sorted(key for key in plan if held.get(key) != plan[key])
            if differs:
                raise EvalError(
                    f"the comparison in {self.state} was pre-registered with another design ({', '.join(differs)} "
                    "differ); start a new --state-dir"
                )
            return
        self.state.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**plan, "registered": now()}, indent=2) + "\n")
        lines = [
            f"# Pre-registration: {self.design.name}", "",
            f"Registered {now()}, before any run.", "",
            f"- **Hypothesis:** {plan['hypothesis']}",
            f"- **Metric:** {plan['metric']}",
            f"- **Reading:** {plan['reading']}",
            f"- **Arms:** {', '.join(self.arms)}; seeds {', '.join(str(seed) for seed in self.seeds)}; model {self.model}",
            f"- **Per run:** ${self.max_usd:.2f}, {self.hours:g} h, the assay arm up to {self.max_sessions} sessions",
            f"- **The goal arm's condition:** {self.design.raw['goal']}",
            f"- **The design file:** compare.json, sha256 {plan['design_sha256']}",
        ]
        (self.state / "PREREGISTRATION.md").write_text("\n".join(lines) + "\n")

    # ---- shared ---------------------------------------------------------------------
    def env(self) -> dict[str, str]:
        secrets = set(self.design.registry.get("secrets") or [])
        env = {key: value for key, value in os.environ.items()
               if key not in secrets and key not in ("ASSAY_OWNER_TOKEN_FILE", "ASSAY_STOP_GATE")}
        env["ASSAY_ANCHOR_DIR"] = str(self.state / "anchors")
        return env

    def check(self, arm: str, values: dict[str, str]) -> list[str]:
        return _words(list(self.design.raw["check"][arm]), {"python": sys.executable, "pack": str(self.design.pack), **values})

    def assay(self, *words: str, run: Path) -> subprocess.CompletedProcess[str]:
        env = {**self.env(), "ASSAY": shlex.join(self.launcher)}
        return subprocess.run([*self.launcher, "--run-dir", str(run), *words], capture_output=True, text=True,
                              env=env, timeout=900, check=False)

    def host_version(self, arm: str) -> str:
        custom = self.goal_player if arm == "goal" else self.assay_player
        if custom:
            # The script a `{python} script ...` command runs names it.
            words = [word for word in custom if not word.startswith("-")]
            named = words[1] if len(words) > 1 and Path(words[0]).name in ("{python}", "python", "python3") else words[0]
            return Path(named).name
        try:
            done = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=60, check=False)
        except FileNotFoundError:
            raise EvalError("the host `claude` is not on PATH") from None
        return done.stdout.strip() or "unknown"

    # ---- the assay arm --------------------------------------------------------------
    def assay_prompt_text(self) -> str:
        if self.assay_prompt is not None:
            return self.assay_prompt.read_text()
        has_win = self.design.recipe["world"].get("win") is not None
        stopping = (
            "Stop at WIN, at the cap, or when an action needs the owner's approval. If you judge the goal unreachable "
            "with what is left, or you need something only the owner can give, first add one line to .assay/NOTES.md "
            "that begins with STOP: and says why. A stop for any other reason is refused or restarted."
            if has_win else
            "This world has no WIN. When you judge the task done, or at the cap, add one line to .assay/NOTES.md that "
            "begins with STOP: and says why, then stop. A stop for any other reason is refused or restarted."
        )
        return fill((PROMPTS / "compare_assay.md").read_text(), {
            "CONSTITUTION": str(CONSTITUTION),
            "TASK": str(self.design.registry["goal"]["text"]),
            "NOTE": str(self.design.recipe.get("player", {}).get("note", "")),
            "BUDGET": str(self.design.registry["budget"]["actions"]),
            "STOPPING": stopping,
        })

    def assay_arm(self, seed: int) -> dict[str, Any]:
        run = self.state / "runs" / f"assay-s{seed}"
        token = self.state / "tokens" / f"assay-s{seed}.token"
        policy = self.state / "tokens" / f"assay-s{seed}.hooks.json"
        token.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(token.parent, 0o700)
        if not (run / ".assay" / "config.json").is_file():
            run.mkdir(parents=True, exist_ok=True)
            started = self.assay("start", str(self.design.raw["world"]), "--adapter",
                                 f"{self.design.pack / 'adapter.py'}:factory", "--registry",
                                 str(self.design.pack / "registry.json"), "--seed", str(seed),
                                 "--owner-token-file", str(token), run=run)
            if started.returncode != 0:
                raise EvalError(f"the assay arm's start failed: {(started.stderr or started.stdout).strip()[-500:]}")
            deny = [word for pattern in self.design.raw.get("deny", []) for word in ("--deny", pattern)]
            hooked = self.assay("hooks", "install", "--policy", str(policy), "--owner-token-file", str(token), *deny, run=run)
            if hooked.returncode != 0:
                raise EvalError(f"the assay arm's hooks failed: {(hooked.stderr or hooked.stdout).strip()[-500:]}")
            (run / "PLAYER.md").write_text(self.assay_prompt_text())
        argv = [sys.executable, str(REPO / "tools" / "eval" / "__main__.py"), "session", "--run-dir", str(run),
                "--model", self.model, "--hours", str(self.hours), "--max-sessions", str(self.max_sessions),
                "--max-usd", str(self.max_usd), "--assay", shlex.join(self.launcher)]
        argv += ["--", *self.assay_player] if self.assay_player else ["--player", "claude"]
        log = self.state / "logs" / f"assay-s{seed}.session.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        began = time.monotonic()
        with log.open("w") as handle:
            subprocess.run(argv, cwd=REPO, env=self.env(), stdout=handle, stderr=subprocess.STDOUT,
                           timeout=self.hours * 3600 * self.max_sessions + 1800, check=False)
        wall = round(time.monotonic() - began, 1)
        status = json.loads((run.with_name(run.name + ".sessions") / "status.json").read_text()) if (
            run.with_name(run.name + ".sessions") / "status.json").is_file() else {}
        # A run that ended at a declared stop or an owner action keeps its
        # daemon up for a resume; the comparison is over, so it goes down,
        # and the audit records the anchors for the report.
        self.assay("stop", run=run)
        self.assay("audit", run=run)
        record = build_report(run, check=shlex.join(self.check("assay", {"run_dir": str(run), "seed": str(seed)})))
        document = self.state / "reports" / f"assay-s{seed}.report.md"
        write_report(record, document, 25)
        outcome = str(status.get("outcome") or record["ending"]["outcome"])
        check = record["check"] or {}
        totals = (record["sessions"] or {}).get("totals") or {}
        return {
            "arm": "assay", "seed": seed, "pack": self.design.name, "model": self.model,
            "host": self.host_version("assay"), "outcome": outcome, "ended_by": outcome,
            "claimed": outcome in CLAIMED["assay"], "passed": bool(check.get("passed")),
            "verdict": (check.get("result") or {}).get("verdict"), "check": check.get("result"),
            "dollars": totals.get("dollars"), "tokens": totals.get("tokens"), "sessions": totals.get("sessions"),
            "paid_actions": record["ending"]["paid"], "wall_seconds": wall,
            "run_dir": str(run), "report": str(document), "log": str(log),
        }

    # ---- the goal arm ---------------------------------------------------------------
    def goal_world(self, seed: int) -> tuple[Path, dict[str, Any]]:
        """The bare copy of the world for one seed, and world.py beside it."""
        directory = self.state / "runs" / f"goal-s{seed}"
        scratch = self.state / "goal-state" / f"s{seed}"
        world = self.design.recipe["world"]
        if directory.exists():
            shutil.rmtree(directory)
        if scratch.exists():
            shutil.rmtree(scratch)
        directory.mkdir(parents=True)
        scratch.mkdir(parents=True)
        workdir = directory / ("work" if self.design.mode == "worktree" else "world")
        source = world.get("source")
        if source:
            shutil.copytree(self.design.pack / source, workdir, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            workdir.mkdir()
        values = {"python": sys.executable, "recipe": str(self.design.pack), "archetypes": str(REPO / "worlds" / "archetypes"),
                  "workdir": str(workdir), "state": str(scratch), "seed": str(seed)}
        env = {key: value for key, value in self.env().items() if not key.startswith("ASSAY")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if world.get("setup"):
            done = subprocess.run(_words(world["setup"], values), cwd=workdir, capture_output=True, text=True,
                                  env=env, timeout=float(world.get("timeout_seconds", 30)), check=False)
            if done.returncode != 0:
                raise EvalError(f"the goal arm's world setup failed: {done.stderr.strip()[-500:]}")
        config = {
            "workdir": str(workdir), "observe": _words(world["observe"], values), "act": _words(world["act"], values),
            "actions": list(world["actions"]), "timeout": float(world.get("timeout_seconds", 30)),
            "log": str(scratch / "acts.jsonl"),
        }
        (directory / "world.py").write_text(WORLD_SCRIPT.format(config=json.dumps(config)))
        return directory, config

    def goal_prompt(self) -> str:
        return fill((PROMPTS / "compare_goal.md").read_text(), {
            "GOAL": str(self.design.raw["goal"]),
            "TASK": str(self.design.raw.get("task") or self.design.registry["goal"]["text"]),
            "ACCESS": str(self.design.raw["access"]),
        })

    def goal_argv(self, prompt: str, directory: Path, seed: int) -> list[str]:
        if self.goal_player:
            return _words(self.goal_player, {"prompt": prompt, "model": self.model, "usd": str(self.max_usd),
                                             "python": sys.executable, "repo": str(REPO), "dir": str(directory),
                                             "seed": str(seed)})
        return ["claude", "-p", prompt, "--model", self.model, "--output-format", "stream-json", "--verbose",
                "--max-budget-usd", str(self.max_usd), "--permission-mode", "acceptEdits",
                "--allowedTools", *ALLOWED_TOOLS, "--disallowedTools", *DISALLOWED_TOOLS,
                "--strict-mcp-config", "--no-session-persistence"]

    def replayed(self, seed: int, config: dict[str, Any]) -> bool | None:
        """Whether the copy is what its logged actions make of a fresh one
        (the copy mode only; None for a worktree, which is edited by design)."""
        if self.design.mode != "copy":
            return None
        fresh_root = self.state / "goal-state" / f"s{seed}-replay"
        if fresh_root.exists():
            shutil.rmtree(fresh_root)
        world = self.design.recipe["world"]
        fresh = fresh_root / "world"
        scratch = fresh_root / "state"
        scratch.mkdir(parents=True)
        source = world.get("source")
        if source:
            shutil.copytree(self.design.pack / source, fresh, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            fresh.mkdir()
        values = {"python": sys.executable, "recipe": str(self.design.pack), "archetypes": str(REPO / "worlds" / "archetypes"),
                  "workdir": str(fresh), "state": str(scratch), "seed": str(seed)}

        env = {key: value for key, value in self.env().items() if not key.startswith("ASSAY")}

        def run(argv: list[str], stdin: str | None = None) -> str:
            return subprocess.run(argv, cwd=fresh, input=stdin, capture_output=True, text=True, env=env,
                                  timeout=config["timeout"], check=False).stdout.strip()

        if world.get("setup"):
            run(_words(world["setup"], values))
        log = Path(config["log"])
        for line in log.read_text().splitlines() if log.is_file() else []:
            act = json.loads(line)
            run([act["action"] if word == "{action}" else word for word in _words(world["act"], values)],
                json.dumps(act["params"]))
        mine = subprocess.run(config["observe"], cwd=config["workdir"], capture_output=True, text=True, env=env,
                              timeout=config["timeout"], check=False).stdout.strip()
        return run(_words(world["observe"], values)) == mine

    def goal_arm(self, seed: int) -> dict[str, Any]:
        directory, config = self.goal_world(seed)
        prompt = self.goal_prompt()
        argv = self.goal_argv(prompt, directory, seed)
        out = self.state / "logs" / f"goal-s{seed}.stream.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        env = {key: value for key, value in self.env().items() if not key.startswith("ASSAY")}
        began = time.monotonic()
        timed_out = False
        with out.open("w") as handle:
            proc = subprocess.Popen(argv, cwd=directory, env=env, stdin=subprocess.PIPE, stdout=handle,
                                    stderr=subprocess.STDOUT, text=True, start_new_session=True)
            try:
                proc.communicate(prompt, timeout=self.hours * 3600)
            except subprocess.TimeoutExpired:
                timed_out = True
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(proc.pid, sig)
                    except ProcessLookupError:
                        break
                    try:
                        proc.wait(timeout=10)
                        break
                    except subprocess.TimeoutExpired:
                        continue
        wall = round(time.monotonic() - began, 1)
        ending = goal_ending(out.read_text(errors="replace"), timed_out=timed_out)
        argv_check = self.check("goal", {"dir": str(directory), "seed": str(seed)})
        done = subprocess.run(argv_check, capture_output=True, text=True, timeout=900, check=False)
        result: Any = None
        try:
            result = json.loads(done.stdout)
        except ValueError:
            result = {"output": (done.stdout + done.stderr)[-2000:]}
        acts = sum(1 for _ in Path(config["log"]).open()) if Path(config["log"]).is_file() else 0
        return {
            "arm": "goal", "seed": seed, "pack": self.design.name, "model": self.model,
            "host": self.host_version("goal"), "outcome": ending["ended_by"], "ended_by": ending["ended_by"],
            "claimed": ending["ended_by"] in CLAIMED["goal"], "passed": done.returncode == 0,
            "verdict": result.get("verdict") if isinstance(result, dict) else None, "check": result,
            "dollars": ending["dollars"], "tokens": ending["tokens"], "turns": ending["turns"],
            "goal_feedback": ending["goal_feedback"], "acts": acts, "copy_replays": self.replayed(seed, config),
            "wall_seconds": wall, "run_dir": str(directory), "log": str(out), "final_message": ending["result"],
        }

    # ---- the whole comparison --------------------------------------------------------
    def rows(self) -> list[dict[str, Any]]:
        path = self.state / "results.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def save(self, rows: list[dict[str, Any]]) -> None:
        rows = sorted(rows, key=lambda row: (row["seed"], ARMS.index(row["arm"])))
        (self.state / "results.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
        (self.state / "COMPARE.md").write_text(summary(self.plan(), rows))

    def dry_run(self) -> int:
        plan = self.plan()
        print(f"COMPARE | {self.design.name} | arms {', '.join(self.arms)} | seeds {self.seeds} | model {self.model} | "
              f"${self.max_usd:.2f} and {self.hours:g} h per run | state {self.state}")
        print(f"HYPOTHESIS | {plan['hypothesis']}")
        for seed in self.seeds:
            if "assay" in self.arms:
                print(f"ASSAY | seed {seed} | start {self.design.raw['world']} --seed {seed}, hooks with deny "
                      f"{self.design.raw.get('deny', [])}, then the session mode | check "
                      f"{shlex.join(self.check('assay', {'run_dir': '<run dir>', 'seed': str(seed)}))}")
            if "goal" in self.arms:
                print(f"GOAL | seed {seed} | {shlex.join(self.goal_argv('<prompt>', Path('<dir>'), seed))} | check "
                      f"{shlex.join(self.check('goal', {'dir': '<dir>', 'seed': str(seed)}))}")
        print("PROMPT | goal arm:\n" + self.goal_prompt())
        return 0

    def run(self) -> int:
        self.preregister()
        rows = self.rows()
        done = {(row["arm"], row["seed"]) for row in rows}
        for seed in self.seeds:
            for arm in self.arms:
                if (arm, seed) in done:
                    print(f"SKIP | {arm} | seed {seed} | already in results.jsonl", flush=True)
                    continue
                print(f"RUN | {arm} | seed {seed} | {now()}", flush=True)
                row = self.assay_arm(seed) if arm == "assay" else self.goal_arm(seed)
                rows.append(row)
                self.save(rows)
                cost = f"${row['dollars']:.2f}" if isinstance(row.get("dollars"), (int, float)) else "cost not reported"
                print(f"ROW | {arm} | seed {seed} | ended {row['ended_by']} | claimed {row['claimed']} | checker "
                      f"{row['verdict'] or ('pass' if row['passed'] else 'fail')} | {cost}", flush=True)
        self.save(rows)
        for line in totals_lines(rows, self.arms):
            print(line, flush=True)
        print(f"WROTE | {self.state / 'COMPARE.md'} | {self.state / 'results.jsonl'}", flush=True)
        return 0


def arm_totals(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    mine = [row for row in rows if row["arm"] == arm]
    dollars = [row["dollars"] for row in mine if isinstance(row.get("dollars"), (int, float))]
    return {
        "runs": len(mine),
        "claimed": sum(1 for row in mine if row["claimed"]),
        "passed": sum(1 for row in mine if row["passed"]),
        "false_completions": sum(1 for row in mine if row["claimed"] and not row["passed"]),
        "dollars": round(sum(dollars), 4) if dollars else None,
    }


def totals_lines(rows: list[dict[str, Any]], arms: list[str]) -> list[str]:
    lines = []
    for arm in arms:
        total = arm_totals(rows, arm)
        cost = f"${total['dollars']:.2f}" if total["dollars"] is not None else "cost not reported"
        lines.append(f"ARM | {arm} | runs {total['runs']} | claimed {total['claimed']} | checker passed "
                     f"{total['passed']} | false completions {total['false_completions']} | {cost}")
    return lines


def summary(plan: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """COMPARE.md: the design as registered, the totals per arm, every row."""
    out = [f"# Comparison: {plan['pack']}", "",
           f"- **Hypothesis:** {plan['hypothesis']}", f"- **Metric:** {plan['metric']}",
           f"- **Reading:** {plan['reading']}",
           f"- **Model:** {plan['model']}; ${plan['max_usd_per_run']:.2f} and {plan['hours_per_run']:g} h per run.",
           "", "| Arm | Runs | Claimed the goal | Checker passed | False completions | Dollars |", "|---|---|---|---|---|---|"]
    for arm in plan["arms"]:
        total = arm_totals(rows, arm)
        cost = f"${total['dollars']:.2f}" if total["dollars"] is not None else "not reported"
        out.append(f"| {arm} | {total['runs']} | {total['claimed']} | {total['passed']} | {total['false_completions']} | {cost} |")
    out += ["", "| Arm | Seed | Ended by | Claimed | Checker | Dollars | Host |", "|---|---|---|---|---|---|---|"]
    for row in rows:
        cost = f"${row['dollars']:.2f}" if isinstance(row.get("dollars"), (int, float)) else ""
        verdict = row.get("verdict") or ("pass" if row["passed"] else "fail")
        out.append(f"| {row['arm']} | {row['seed']} | {row['ended_by']} | {'yes' if row['claimed'] else 'no'} | "
                   f"{verdict} | {cost} | {row.get('host', '')} |")
    never = [(row["arm"], row["seed"], row["check"]["coverage"]["never_tested"]) for row in rows
             if isinstance(row.get("check"), dict) and isinstance(row["check"].get("coverage"), dict)]
    if never:
        out += ["", "Rules each run never tested with a sentence that broke only that rule:", ""]
        out += [f"- {arm}, seed {seed}: {', '.join(kinds) or 'none'}" for arm, seed, kinds in never]
    edited = [(row["seed"]) for row in rows if row.get("copy_replays") is False]
    if edited:
        out += ["", "The goal arm changed its copy outside world.py at seed(s) " + ", ".join(str(seed) for seed in edited) + "."]
    out += ["", f"Read as registered: {plan['reading']}"]
    return "\n".join(out) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m tools.eval compare",
        description="Compare ASSAY with Claude Code and /goal on one pack: the same model, dollars, hours and checker.",
    )
    parser.add_argument("--pack", type=Path, required=True, metavar="DIR", help="a pack directory holding compare.json")
    parser.add_argument("--seeds", type=int, nargs="+", metavar="N", help="default: the design's")
    parser.add_argument("--model", help="default: the design's")
    parser.add_argument("--max-usd", type=float, metavar="USD", help="per run (default: the design's)")
    parser.add_argument("--hours", type=float, metavar="H", help="per run (default: the design's)")
    parser.add_argument("--max-sessions", type=int, metavar="N", help="the assay arm's sessions per run (default: the design's, or 3)")
    parser.add_argument("--arms", default="assay,goal", help="assay, goal or both (default: assay,goal)")
    parser.add_argument("--state-dir", type=Path, metavar="DIR", help="default: ./compare-state/<pack>")
    parser.add_argument("--assay", metavar="CMD", help=f"the launcher, split like a shell line (default: {DEFAULT_LAUNCHER})")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and the commands; write nothing")
    parser.add_argument("--goal-player", metavar="CMD", help="replace the goal arm's claude command (tests)")
    parser.add_argument("--assay-player", metavar="CMD", help="replace the assay arm's player command (tests)")
    parser.add_argument("--assay-prompt", type=Path, metavar="FILE", help="replace the assay arm's prompt (tests)")
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        design = load_design(args.pack)
        raw = design.raw
        arms = [arm.strip() for arm in args.arms.split(",") if arm.strip()]
        if not arms or any(arm not in ARMS for arm in arms):
            raise EvalError("--arms takes assay, goal or both")
        max_usd = args.max_usd if args.max_usd is not None else float(raw["max_usd"])
        hours = args.hours if args.hours is not None else float(raw["hours"])
        sessions = args.max_sessions if args.max_sessions is not None else int(raw.get("max_sessions", 3))
        if max_usd <= 0 or hours <= 0 or sessions < 1:
            raise EvalError("--max-usd and --hours must be positive and --max-sessions at least 1")
        compare = Compare(
            design, state_dir=args.state_dir or Path("compare-state") / design.name,
            seeds=args.seeds or [int(seed) for seed in raw["seeds"]], model=args.model or str(raw["model"]),
            max_usd=max_usd, hours=hours, max_sessions=sessions, arms=arms,
            launcher=shlex.split(args.assay) if args.assay else [str(DEFAULT_LAUNCHER)],
            goal_player=shlex.split(args.goal_player) if args.goal_player else None,
            assay_player=shlex.split(args.assay_player) if args.assay_player else None,
            assay_prompt=args.assay_prompt,
        )
        if args.dry_run:
            return compare.dry_run()
        return compare.run()
    except EvalError as error:
        print(f"ERROR | {error}", file=sys.stderr, flush=True)
        return 2
    except subprocess.TimeoutExpired as error:
        print(f"ERROR | a step ran past its time limit: {error}", file=sys.stderr, flush=True)
        return 2



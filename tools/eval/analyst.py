"""The analyst: a separate model that reads a run after the stall rule fired
and returns ranked directions for the player's next session.

The session runner's supervisor calls it before the relaunch. It reads the
run and writes nothing to it. Its input is one JSON object, the bundle:

- `diagnosis`: the findings of `diagnose.diagnose(report.build(run))`, by
  layer (1 the constitution, 2 the modules, 3 the registry and the adapter,
  4 the addressable states, 5 the model);
- `world_model`: the prediction meter, per action and outcome form the held
  and missed counts with the latest misses and what the world reported,
  the coverage block, the declared states and the tail of `NOTES.md`;
- `frontier`: the best cycles, the progress anchor, the last 40
  evaluations, the count per failure stage, and every evaluation that
  improved the frontier;
- `failed_directions`: the earlier analyst directions given since the last
  meaningful improvement, each with the labels of the evaluations made
  after it. As in RSI-top, the list empties at a meaningful improvement.

When the report cannot be built the bundle says why and carries the rest.

The call is a subprocess built from a player host's template (`claude`,
`codex`, `opencode`) or a custom command, in a scratch directory under
`<state dir>/analyst/`, with the runner's environment minus the launcher,
the owner token, the Stop gate's variable and the world's secrets, and a
timeout. Its output is parsed tolerantly: the JSON object itself, a `claude
-p --output-format json` report whose `result` holds it, JSON lines, or
prose around a fenced block. A failed call, a time-out or output with no
valid direction is recorded and yields no directions; it never ends the run.

The records, in `<state dir>/analyst/`: `a<k>.input.json` (the bundle),
`a<k>.prompt.md`, `a<k>.out`, `a<k>.err`, and `a<k>.json` (the call: the
command as a shell line, the host and its version, the model, the times,
the exit code, whether it timed out, the dollars and tokens when the output
reports them, the directions or the reason there are none).
"""

from __future__ import annotations

import dataclasses
import json
import re
import shlex
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import EvalError
from .diagnose import LAYERS, diagnose
from .frontier import Evaluation, Intervention, StallState, iso
from .report import build
from .results import player_report, session_tokens, stream_spend
from .runner import REPO, now
from .session import HOSTS, VERSION_TIMEOUT_SECONDS, expand, kill_group

PROMPT = REPO / "tools" / "eval" / "prompts" / "analyst.md"
PLACEHOLDER = "{{INPUT}}"
TIMEOUT_MINUTES = 20.0
MAX_DIRECTIONS = 5
TEXT_LIMIT = 2000
NOTES_LIMIT = 12000
CONTEXT_LIMIT = 60000
RECENT_EVALUATIONS = 40
RECENT_MISSES = 3
LABELS_LIMIT = 40
DIRECTIONS_AT = re.compile(r'\{\s*"directions"\s*:')
CUSTOM_VALUES = ("prompt", "prompt_file", "model")


# ---- the call's configuration -----------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AnalystConfig:
    """A host's template, or a custom command split like a shell line with
    `{prompt}`, `{prompt_file}` and `{model}`."""

    host: str
    template: tuple[str, ...]
    options: tuple[str, ...]
    custom: bool
    model: str | None = None
    prompt: Path = PROMPT
    timeout_seconds: float = TIMEOUT_MINUTES * 60.0
    context: tuple[Path, ...] = ()

    @classmethod
    def from_host(cls, name: str, **settings: Any) -> AnalystConfig:
        if name not in HOSTS:
            raise EvalError(f"--analyst {name} is not one of {', '.join(sorted(HOSTS))}")
        host = HOSTS[name]
        return cls(host=name, template=host.command, options=host.model, custom=False, **settings)

    @classmethod
    def from_command(cls, text: str, **settings: Any) -> AnalystConfig:
        try:
            words = tuple(shlex.split(text))
        except ValueError as error:
            raise EvalError(f"--analyst-command does not split like a shell line: {error}") from None
        if not words:
            raise EvalError("--analyst-command must name a command")
        try:
            expand(words, dict.fromkeys(CUSTOM_VALUES, ""))
        except KeyError as error:
            raise EvalError(
                f"--analyst-command names {{{error.args[0]}}}; it knows {', '.join('{' + n + '}' for n in CUSTOM_VALUES)}"
            ) from None
        name = Path(words[0]).name
        if name.startswith("python") and len(words) > 1 and not words[1].startswith("-"):
            name = Path(words[1]).name
        return cls(host=name, template=words, options=(), custom=True, **settings)

    def argv(self, prompt: str, prompt_file: Path) -> list[str]:
        values = {"prompt": prompt, "prompt_file": str(prompt_file), "model": self.model or ""}
        return expand(self.template, values, self.options if self.model else ())

    def shell_line(self, prompt_file: Path) -> str:
        """The command as a shell line, the prompt shown as its file."""
        marker = "\0PROMPT\0"
        shown = shlex.quote(str(prompt_file))
        text = " ".join(
            f'"$(cat {shown})"' if word == marker else shlex.quote(word) for word in self.argv(marker, prompt_file)
        )
        return text + (f" < {shown}" if self.custom else "")

    def template_text(self) -> str:
        try:
            text = self.prompt.read_text()
        except OSError as error:
            raise EvalError(f"the analyst's prompt {self.prompt} cannot be read: {error}") from None
        if PLACEHOLDER not in text:
            raise EvalError(f"the analyst's prompt {self.prompt} has no {PLACEHOLDER} placeholder")
        return text

    def version(self) -> str:
        """The first line `COMMAND --version` prints; a named host that does
        not run is refused before anything is touched."""
        argv = [self.template[0], "--version"]
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, timeout=VERSION_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise EvalError(f"the analyst's command `{argv[0]}` is not found") from None
        except (OSError, subprocess.TimeoutExpired) as error:
            raise EvalError(f"`{shlex.join(argv)}` did not run: {error}") from None
        said = (done.stdout.strip() or done.stderr.strip()).splitlines()
        first = said[0].strip() if said else ""
        if done.returncode != 0:
            if self.custom:
                return first or f"unknown (exit {done.returncode})"
            raise EvalError(f"the analyst's host does not run: `{shlex.join(argv)}` exited {done.returncode}: {first}")
        return first


# ---- the bundle ---------------------------------------------------------------------------


def _tail(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else "[earlier notes cut]\n" + text[-limit:]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def world_model(record: Mapping[str, Any], run_dir: Path) -> dict[str, Any]:
    predictions = record["predictions"]
    misses: dict[int, list[dict[str, Any]]] = {}
    for item in predictions["missed"]:
        misses.setdefault(item["event"], []).append({"event": item["event"], "outcome": item["outcome"], "actual": item["actual"]})
    actions: dict[str, Any] = {}
    for action, slot in predictions["actions"].items():
        forms = {}
        for form, entry in slot["forms"].items():
            latest = entry["missed"][-RECENT_MISSES:]
            forms[form] = {
                "held": len(entry["held"]), "missed": len(entry["missed"]),
                "latest_misses": [miss for event in latest for miss in misses.get(event, [])],
            }
        actions[action] = {"paid": slot["paid"], "changed": slot["changed"], "forms": forms}
    store = run_dir / record["run"]["state_dir"]
    states = _read(store / "channels.json")
    try:
        declared = json.loads(states) if states is not None else None
    except ValueError:
        declared = None
    return {
        "meter": predictions["meter"],
        "meter_line": predictions["line"],
        "actions": actions,
        "coverage": record["coverage"],
        "declared_states": declared,
        "notes": _tail(_read(store / "NOTES.md"), NOTES_LIMIT),
    }


def frontier_view(records: Sequence[Evaluation], state: StallState) -> dict[str, Any]:
    stages = Counter(record.failure_stage or ("none" if record.valid else "unstated") for record in records)
    best = [record.best_cycles for record in records if record.best_cycles is not None]
    return {
        "best_cycles": best[-1] if best else None,
        "anchor_cycles": state.anchor_cycles,
        "evaluations": len(records),
        "valid": sum(1 for record in records if record.valid),
        "recent": [record.row() for record in records[-RECENT_EVALUATIONS:]],
        "failure_stages": dict(sorted(stages.items())),
        "improvements": [record.row() for record in records if record.improved],
    }


def failed_directions(records: Sequence[Evaluation], state: StallState) -> list[dict[str, Any]]:
    """The directions given since the last meaningful improvement, each with
    the labels of the evaluations made between it and the next call."""
    out = []
    for item in state.interventions:
        directions = (item.advice or {}).get("directions")
        if item.record_n < state.anchor_n or not directions:
            continue
        later = [other.record_n for other in state.interventions if other.index > item.index]
        until = later[0] if later else None
        labels = [record.label for record in records if record.n > item.record_n and (until is None or record.n <= until)]
        out.append({
            "call": item.index, "given_at": iso(item.fired_at), "after_evaluation": item.record_n,
            "directions": directions, "evaluations_after": labels[-LABELS_LIMIT:],
        })
    return out


def bundle(
    run_dir: Path, *, sessions_dir: Path, records: Sequence[Evaluation], state: StallState, intervention: Intervention,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "run": str(run_dir),
        "intervention": {
            "index": intervention.index, "fired_at": iso(intervention.fired_at), "after_evaluation": intervention.record_n,
            "anchor_cycles": intervention.anchor_cycles, "active_minutes": round(intervention.active_seconds / 60, 1),
            "attempts_since_anchor": intervention.attempts,
        },
    }
    try:
        record = build(run_dir, sessions_dir=sessions_dir)
        found = diagnose(record)
        out["diagnosis"] = {
            "outcome": record["ending"]["outcome"],
            "findings": [{**item, "layer_name": LAYERS[item["layer"]]} for item in found],
        }
        out["world_model"] = world_model(record, run_dir)
    # The analyst reads what there is: a record the report cannot read must
    # not cost the run its advice, and a 12-hour run must not end here.
    except Exception as error:
        reason = f"the report could not be built: {type(error).__name__}: {error}"
        out["diagnosis"] = {"error": reason}
        out["world_model"] = {"error": reason, "notes": _tail(_read(run_dir / ".assay" / "NOTES.md"), NOTES_LIMIT)}
    out["frontier"] = frontier_view(records, state)
    out["failed_directions"] = failed_directions(records, state)
    return out


def context_files(paths: Sequence[Path]) -> list[dict[str, Any]]:
    """The operator's context files as they stand at the call (the program
    under work, the task's own description): the name and the text, cut at
    the head when long. A file that is not there yet is listed without text."""
    out = []
    for path in paths:
        text = _read(path)
        if text is not None and len(text) > CONTEXT_LIMIT:
            text = text[:CONTEXT_LIMIT] + "\n[the rest is cut]"
        out.append({"file": path.name, "text": text})
    return out


def render(template: str, data: Mapping[str, Any]) -> str:
    return template.replace(PLACEHOLDER, json.dumps(data, indent=2))


# ---- the answer ---------------------------------------------------------------------------


def _objects(value: Any, depth: int = 0) -> Iterator[Mapping[str, Any]]:
    """Every object holding a `directions` list, wherever `value` nests it,
    strings that hold JSON included."""
    if depth > 8:
        return
    if isinstance(value, Mapping):
        if isinstance(value.get("directions"), list):
            yield value
            return
        for item in value.values():
            yield from _objects(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _objects(item, depth + 1)
    elif isinstance(value, str) and "directions" in value:
        yield from _in_text(value, depth + 1)


def _in_text(text: str, depth: int) -> Iterator[Mapping[str, Any]]:
    try:
        yield from _objects(json.loads(text), depth)
        return
    except ValueError:
        pass
    from_lines: list[Mapping[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                from_lines += list(_objects(json.loads(line), depth))
            except ValueError:
                continue
    if from_lines:
        yield from from_lines
        return
    decoder = json.JSONDecoder()
    for match in DIRECTIONS_AT.finditer(text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        yield from _objects(value, depth)


def _bounded(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > TEXT_LIMIT:
        return None
    return value.strip()


def directions_of(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The valid directions, ranked, at most five."""
    kept = []
    for position, item in enumerate(candidate.get("directions") or []):
        if not isinstance(item, Mapping):
            continue
        what, why, layer, rank = _bounded(item.get("try")), _bounded(item.get("why")), item.get("layer"), item.get("rank")
        if what is None or why is None or isinstance(layer, bool) or not isinstance(layer, int) or layer not in LAYERS:
            continue
        order = rank if isinstance(rank, int) and not isinstance(rank, bool) else position + 1
        kept.append((order, position, {"try": what, "why": why, "layer": layer}))
    kept.sort(key=lambda entry: (entry[0], entry[1]))
    return [{"rank": index, **entry} for index, (_, _, entry) in enumerate(kept[:MAX_DIRECTIONS], 1)]


def parse(stdout: str) -> tuple[list[dict[str, Any]], str | None]:
    """The directions and, when there are none, why."""
    found = list(_in_text(stdout, 0)) if stdout.strip() else []
    if not found:
        return [], "the output holds no JSON object with directions"
    for candidate in reversed(found):
        directions = directions_of(candidate)
        if directions:
            return directions, None
    return [], "no direction in the output has a try, a why and a layer from 1 to 5"


def spend(stdout: str) -> tuple[float | None, dict[str, int] | None]:
    report = player_report(stdout)
    if report is not None:
        cost = report.get("total_cost_usd")
        dollars = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
        return dollars, session_tokens(report)
    return stream_spend(stdout)


# ---- the call -----------------------------------------------------------------------------


def call(
    config: AnalystConfig,
    *,
    index: int,
    fired_at: float,
    data: Mapping[str, Any],
    directory: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
    version: str,
    stop: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """One call, its files written; the record `a<k>.json` holds."""
    directory.mkdir(parents=True, exist_ok=True)
    name = f"a{index}"
    files = {suffix: directory / f"{name}.{suffix}" for suffix in ("input.json", "prompt.md", "out", "err", "json")}
    files["input.json"].write_text(json.dumps(data, indent=2) + "\n")
    prompt = render(config.template_text(), data)
    files["prompt.md"].write_text(prompt)
    work = directory / f"{name}.work"
    work.mkdir(exist_ok=True)
    started, clock = now(), time.monotonic()
    code: int | None = None
    timed_out = interrupted = False
    reason: str | None = None
    with files["out"].open("wb") as out, files["err"].open("wb") as err:
        stdin: Any = files["prompt.md"].open("rb") if config.custom else subprocess.DEVNULL
        try:
            proc = subprocess.Popen(
                config.argv(prompt, files["prompt.md"]), cwd=work, env=dict(env), stdin=stdin, stdout=out, stderr=err,
                start_new_session=True,
            )
        except OSError as error:
            reason = f"the analyst could not start: {error}"
        else:
            limit = clock + timeout_seconds
            while proc.poll() is None:
                if stop():
                    interrupted = True
                    kill_group(proc)
                    break
                if time.monotonic() >= limit:
                    timed_out = True
                    kill_group(proc)
                    break
                time.sleep(0.2)
            code = int(proc.wait())
        finally:
            if stdin is not subprocess.DEVNULL:
                stdin.close()
    stdout = files["out"].read_text(errors="replace")
    directions: list[dict[str, Any]] = []
    if reason is None:
        if timed_out:
            reason = f"timed out after {timeout_seconds:g} s"
        elif interrupted:
            reason = "interrupted by the runner's stop"
        elif code != 0:
            said = files["err"].read_text(errors="replace").strip().splitlines()
            reason = f"exit {code}" + (f": {said[-1][:200]}" if said else "")
        else:
            directions, reason = parse(stdout)
    dollars, tokens = spend(stdout)
    record = {
        "index": index, "fired_at": iso(fired_at), "command": config.shell_line(files["prompt.md"]),
        "host": config.host, "host_version": version, "model": config.model, "started": started, "ended": now(),
        "wall_seconds": round(time.monotonic() - clock, 1), "exit_code": code, "timed_out": timed_out,
        "interrupted": interrupted, "dollars": dollars, "tokens": tokens, "directions": directions, "reason": reason,
    }
    files["json"].write_text(json.dumps(record, indent=2) + "\n")
    return record


def advice(record: Mapping[str, Any]) -> dict[str, Any]:
    """What the stall state keeps of a call."""
    return {"directions": record["directions"], "reason": record["reason"], "record": f"a{record['index']}.json"}

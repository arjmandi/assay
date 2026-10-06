#!/usr/bin/env python3
"""notes_at.py: reconstruct an agent-authored file (normally .assay/NOTES.md) as it
stood at a cut point, from Claude Code session transcripts.

The agent changed the file with the Write and Edit tools and with Bash heredocs
(`python3 - <<'PY' ... PY` doing string replaces or full writes), read it with
Read, and saw it printed in full by `assay status`. The transcripts record every
call with a timestamp and every tool result. Two kinds of evidence combine:

  backward: the last full content before the boundary (a Write, a whole-file Read
            result, or a complete status printout) with every later mutation
            before the boundary applied on top. Edits apply directly. A heredoc
            edit is REPLAYED: its Python body runs in a throw-away replica run
            directory holding only .assay/<file>, with every occurrence of the
            source run path rewritten to the replica, under `python3 -I`, with a
            deny list and a timeout. The source file's hash is checked before and
            after every replay; the tool aborts if it ever changes.
  forward:  the first observation at or after the boundary and before any later
            mutation. A whole-file Read is exact and wins. A status printout that
            the agent piped through head/tail (or that hit the 120-line bound) is
            PREFIX evidence: the reconstruction must start with it.

Boundary: the first instant that no longer belongs to the cut state. It is the
earlier of (a) the timestamp of event N+1 (--journal/--cut) or --before, and
(b) the first timestamp of any --post-transcript (a session that started after
the operator intervened: its observations count, its edits never do).

Exit codes: 0 reconstructed, 3 could not reconstruct (fall back to
.assay/levels/level-N.md and document the deviation), 2 usage error.
Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

NUMBERED = re.compile(r"^\s*(\d+)\t(.*)$")
STATUS_NOTES_HEAD = re.compile(r"^NOTES \| (\S+) \(edit the file directly; shown in full\)$")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[ \t]*\n(.*?)\n\1(?=\s|$)", re.S)
MUTATION_HINT = re.compile(r">>?|\btee\b|sed -i|\bcp\b|\bmv\b|\.write\w*\(|open\([^)]*['\"][wa]")
STATUS_FILTER = re.compile(r"status[^|\n]*\|[^\n]*\b(head|tail|sed|awk|cut)\b|status[^|\n]*\|\s*grep(?!\s+-v)")
DENY_MODULES = {"subprocess", "shutil", "socket", "urllib", "http", "requests", "ctypes", "signal",
                "multiprocessing", "importlib", "pty", "ftplib", "smtplib", "webbrowser"}
DENY_ATTRS = {"system", "remove", "unlink", "rename", "rmdir", "removedirs", "popen", "kill",
              "execv", "execve", "execvp", "spawn", "spawnl", "startfile", "rmtree", "chmod", "chown"}
DENY_NAMES = {"__import__", "exec", "eval", "compile", "breakpoint"}


def body_violations(body: str) -> list[str]:
    """Static check of a captured python body: imports and calls only, string
    literals are ignored (the notes text itself may say 'socket' or 'signal')."""
    import ast
    try:
        tree = ast.parse(body)
    except SyntaxError as error:
        return [f"syntax: {error}"]
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name.split(".")[0] in DENY_MODULES]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in DENY_MODULES:
                found.append(f"from {node.module} import …")
        elif isinstance(node, ast.Attribute) and node.attr in DENY_ATTRS:
            found.append(f"attribute .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in DENY_NAMES:
            found.append(f"name {node.id}")
    return found


def parse_ts(value: str) -> dt.datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    when = dt.datetime.fromisoformat(text)
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def sha(text: str | bytes) -> str:
    data = text.encode() if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content
                       if isinstance(part, dict) and part.get("type") == "text")
    return ""


def parse_read_result(text: str) -> str | None:
    """A whole-file Read result (`N<TAB>line`) back into the file."""
    lines, expected = [], 1
    for raw in text.splitlines():
        found = NUMBERED.match(raw)
        if not found or int(found.group(1)) != expected:
            if lines:
                break
            continue
        lines.append(found.group(2))
        expected += 1
    return ("\n".join(lines) + "\n") if lines else None


def parse_status_notes(text: str, path: str, command: str) -> tuple[str | None, bool]:
    """The NOTES block of an `assay status` printout: (content, complete)."""
    out, capture, ended = [], False, False
    bounded = False
    for raw in text.splitlines():
        if not capture:
            found = STATUS_NOTES_HEAD.match(raw.strip())
            if found and found.group(1) == path:
                capture = True
            continue
        if raw.startswith("  "):
            body = raw[2:]
            if re.match(r"^… \d+ more$", body.strip()):
                bounded = True
                continue
            if len(body) >= 240:
                bounded = True
            out.append(body)
        else:
            ended = True
            break
    if not capture or not out:
        return None, False
    complete = ended and not bounded and not STATUS_FILTER.search(command)
    return "\n".join(out) + "\n", complete


def heredoc_programs(command: str, name: str, target: str) -> list[dict]:
    """Replayable pieces of a Bash command that touch the file: python bodies and
    direct heredoc writes. Anything else that could mutate is 'unknown'."""
    pieces = []
    for match in HEREDOC.finditer(command):
        body = match.group(2)
        prefix = command[max(0, match.start() - 120):match.start()]
        tail = command[match.end():]
        if re.search(r"python3?\s+-\s*$", prefix):
            if name in body:
                pieces.append({"kind": "python", "body": body})
            continue
        file_target = re.search(r"cat\s*>\s*(\S+)\s*$", prefix)
        if file_target:
            written = file_target.group(1)
            if written == target or written.endswith("/" + name) or written == name or written.endswith(name):
                pieces.append({"kind": "content", "body": body + "\n"})
            elif re.search(r"python3?\s+" + re.escape(written), tail) and name in body:
                pieces.append({"kind": "python", "body": body})
            elif re.search(r"\b(mv|cp)\s+" + re.escape(written) + r"\s+\S*" + re.escape(name), tail):
                pieces.append({"kind": "content", "body": body + "\n"})
    return pieces


class Replayer:
    """Runs a captured python body against a replica of the run directory."""

    def __init__(self, source_root: Path, target: Path):
        self.source_root = source_root
        self.target = target
        self.rel = target.relative_to(source_root)
        self.guard = self.snapshot()

    def snapshot(self) -> dict:
        """(mtime_ns, size) of every file under the source run dir, plus the target's hash."""
        table = {}
        for path in self.source_root.rglob("*"):
            if path.is_file():
                stat = path.stat()
                table[str(path)] = (stat.st_mtime_ns, stat.st_size)
        table["<target-sha256>"] = sha(self.target.read_bytes()) if self.target.exists() else None
        return table

    def check_source(self) -> None:
        if self.snapshot() != self.guard:
            raise SystemExit(f"ABORT: something under the source run dir {self.source_root} changed during replay")

    def run(self, body: str, content: str) -> tuple[str | None, str]:
        violations = body_violations(body)
        if violations:
            return None, "python body refused: " + ", ".join(sorted(set(violations)))
        with tempfile.TemporaryDirectory(prefix="notes_at_replica_") as scratch:
            replica = Path(scratch) / "run"
            (replica / self.rel).parent.mkdir(parents=True, exist_ok=True)
            (replica / self.rel).write_text(content)
            rewritten = body.replace(str(self.source_root), str(replica))
            self.check_source()
            try:
                proc = subprocess.run(
                    [sys.executable, "-I", "-"], input=rewritten, cwd=str(replica),
                    capture_output=True, text=True, timeout=20,
                    env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
                )
            except subprocess.TimeoutExpired:
                return None, "timeout"
            finally:
                self.check_source()
            if proc.returncode != 0:
                return None, f"exit {proc.returncode}: {proc.stderr.strip()[-200:]}"
            result = (replica / self.rel).read_text()
            if result == content:
                return result, "replayed (no change: a replace may have missed)"
            return result, "replayed"


def collect(transcripts: list[tuple[Path, bool]], target: str) -> list[dict]:
    """Every observation/mutation of `target`, time ordered. `post` marks items
    from post-intervention sessions."""
    name = Path(target).name
    items: list[dict] = []
    for transcript, post in transcripts:
        pending: dict[str, dict] = {}
        with transcript.open() as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stamp = record.get("timestamp")
                content = (record.get("message") or {}).get("content")
                if not stamp or not isinstance(content, list):
                    continue
                when = parse_ts(stamp)
                base = {"ts": when, "file": transcript.name, "post": post}
                if record.get("type") == "assistant":
                    for part in content:
                        if not isinstance(part, dict) or part.get("type") != "tool_use":
                            continue
                        tool = part.get("name")
                        args = part.get("input") or {}
                        file_path = str(args.get("file_path", ""))
                        if tool == "Read" and file_path == target:
                            if args.get("offset") is None and args.get("limit") is None:
                                pending[part["id"]] = {"kind": "read", **base}
                        elif tool == "Write" and file_path == target:
                            pending[part["id"]] = {"kind": "mutate", "items": [
                                {"kind": "write", "content": args.get("content", ""), **base}]}
                        elif tool in ("Edit", "MultiEdit") and file_path == target:
                            edits = args.get("edits") if tool == "MultiEdit" else [args]
                            pending[part["id"]] = {"kind": "mutate", "items": [
                                {"kind": "edit", "old": e.get("old_string", ""), "new": e.get("new_string", ""),
                                 "all": bool(e.get("replace_all")), **base} for e in (edits or [])]}
                        elif tool == "Bash":
                            command = str(args.get("command", ""))
                            mentions = name in command or target in command
                            if mentions:
                                programs = heredoc_programs(command, name, target)
                                if programs:
                                    kind = "heredoc"
                                elif MUTATION_HINT.search(command):
                                    kind = "opaque"
                                else:
                                    kind = "bash-read"
                                items.append({"kind": kind, "command": command, "programs": programs, **base})
                            if re.search(r"assay\S*\s+status\b", command):
                                pending[part["id"]] = {"kind": "status", "command": command, **base}
                elif record.get("type") == "user":
                    for part in content:
                        if not isinstance(part, dict) or part.get("type") != "tool_result":
                            continue
                        waiting = pending.pop(part.get("tool_use_id"), None)
                        if not waiting:
                            continue
                        text = text_of(part.get("content"))
                        failed = bool(part.get("is_error")) or "<tool_use_error>" in text or text.lstrip().startswith("Error")
                        if waiting["kind"] == "mutate":
                            if failed:
                                items.append({"kind": "failed-mutation", "detail": text[:160], **base})
                            else:
                                items.extend(waiting["items"])
                        elif waiting["kind"] == "read":
                            parsed = None if failed else parse_read_result(text)
                            if parsed is not None:
                                items.append({"kind": "read", "content": parsed, **{k: waiting[k] for k in ("ts", "file", "post")}})
                        else:
                            parsed, complete = parse_status_notes(text, target, waiting["command"])
                            if parsed is not None:
                                items.append({"kind": "status", "content": parsed, "complete": complete,
                                              **{k: waiting[k] for k in ("ts", "file", "post")}})
    items.sort(key=lambda item: item["ts"])
    return items


def apply_edit(content: str, item: dict) -> str | None:
    old, new = item["old"], item["new"]
    if old not in content:
        return None
    if item["all"]:
        return content.replace(old, new)
    return content.replace(old, new, 1) if content.count(old) == 1 else None


def strip_trailing_blank(text: str) -> list[str]:
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def prefix_matches(observed: str, content: str) -> bool:
    obs = strip_trailing_blank(observed)
    have = content.splitlines()
    return len(obs) > 0 and have[: len(obs)] == obs


def reconstruct(items: list[dict], before: dt.datetime | None, replayer: Replayer | None) -> dict:
    content, certain, source, trail, replays = None, False, None, [], []
    for item in items:
        if before is not None and item["ts"] >= before:
            break
        if item["post"]:
            continue  # cannot happen before the boundary, kept for safety
        stamp = item["ts"].isoformat()
        kind = item["kind"]
        if kind in ("read", "write") or (kind == "status" and item.get("complete")):
            content, certain, source = item["content"], True, f"{kind} @ {stamp} ({item['file']})"
        elif kind == "status":
            if content is not None and not prefix_matches(item["content"], content):
                certain = False
                trail.append(f"status prefix @ {stamp} disagrees with the reconstruction")
        elif kind == "edit":
            applied = apply_edit(content, item) if content is not None else None
            if applied is None:
                certain = False
                trail.append(f"edit @ {stamp} could not be applied")
            else:
                content, source = applied, f"edit @ {stamp} on [{source}]"
        elif kind == "heredoc":
            for program in item["programs"]:
                if program["kind"] == "content":
                    content, certain, source = program["body"], True, f"heredoc write @ {stamp} ({item['file']})"
                    replays.append({"ts": stamp, "result": "heredoc content taken verbatim"})
                    continue
                if content is None or replayer is None:
                    certain = False
                    trail.append(f"heredoc @ {stamp} with no prior content or no replayer")
                    continue
                result, note = replayer.run(program["body"], content)
                replays.append({"ts": stamp, "result": note})
                if result is None:
                    certain = False
                    trail.append(f"heredoc replay @ {stamp} failed: {note}")
                else:
                    content, source = result, f"replayed heredoc @ {stamp} on [{source}]"
        elif kind == "opaque":
            certain = False
            trail.append(f"opaque bash @ {stamp}: {item['command'][:100]!r}")
    forward, forward_source, forward_kind = None, None, None
    prefix_obs = []
    if before is not None:
        for item in items:
            if item["ts"] < before:
                continue
            kind = item["kind"]
            if kind == "read":
                forward, forward_source, forward_kind = item["content"], f"read @ {item['ts'].isoformat()} ({item['file']})", "exact"
                break
            if kind == "status":
                if item.get("complete") and forward is None:
                    forward, forward_source, forward_kind = item["content"], f"status @ {item['ts'].isoformat()} ({item['file']})", "complete-status"
                    break
                prefix_obs.append(item)
                continue
            if kind in ("bash-read", "failed-mutation"):
                continue
            break  # a mutation ends the forward window
    agree = None
    if forward is not None and content is not None:
        agree = strip_trailing_blank(forward) == strip_trailing_blank(content)
    prefix_checks = []
    for item in prefix_obs:
        probe = forward if forward is not None else content
        if probe is not None:
            prefix_checks.append({"ts": item["ts"].isoformat(), "file": item["file"],
                                  "lines": len(strip_trailing_blank(item["content"])),
                                  "matches": prefix_matches(item["content"], probe)})
    report = {
        "backward_certain": certain and content is not None,
        "backward_source": source,
        "backward_uncertainty": trail,
        "replays": replays,
        "forward_source": forward_source,
        "forward_kind": forward_kind,
        "agree": agree,
        "prefix_checks": prefix_checks,
    }
    if forward is not None:
        return {"content": forward, "result_from": "forward observation", **report}
    if certain and content is not None:
        if prefix_checks and not all(check["matches"] for check in prefix_checks):
            return {"content": None, "result_from": None, **report,
                    "backward_uncertainty": trail + ["a post-boundary status prefix contradicts the reconstruction"]}
        return {"content": content, "result_from": "backward reconstruction", **report}
    return {"content": None, "result_from": None, **report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", required=True, help="absolute path of the file as the agent saw it")
    parser.add_argument("--transcript", action="append", default=[], type=Path,
                        help="session transcript from BEFORE the intervention (repeatable)")
    parser.add_argument("--post-transcript", action="append", default=[], type=Path,
                        help="session transcript from AFTER the intervention: observations only (repeatable)")
    parser.add_argument("--before", help="boundary timestamp (ISO 8601)")
    parser.add_argument("--journal", type=Path, help="events.jsonl to derive the boundary from (with --cut)")
    parser.add_argument("--cut", type=int, help="event id of the cut; the boundary is the timestamp of event cut+1")
    parser.add_argument("--out", type=Path, help="write the reconstructed file here (default stdout)")
    parser.add_argument("--report", type=Path, help="write the JSON report here (default stderr)")
    args = parser.parse_args(argv)
    if not args.transcript and not args.post_transcript:
        parser.error("give at least one --transcript or --post-transcript")

    candidates: list[dt.datetime] = []
    if args.before:
        candidates.append(parse_ts(args.before))
    if args.journal is not None and args.cut is not None:
        lines = [line for line in args.journal.read_text().splitlines() if line.strip()]
        if args.cut + 1 < len(lines):
            candidates.append(parse_ts(json.loads(lines[args.cut + 1])["timestamp"]))
    for transcript in args.post_transcript:
        with transcript.open() as handle:
            for line in handle:
                try:
                    stamp = json.loads(line).get("timestamp")
                except json.JSONDecodeError:
                    continue
                if stamp:
                    candidates.append(parse_ts(stamp))
                    break
    before = min(candidates) if candidates else None

    target = Path(args.path)
    source_root = target.parent.parent if target.parent.name == ".assay" else target.parent
    replayer = Replayer(source_root, target) if target.exists() else None
    items = collect([(t, False) for t in args.transcript] + [(t, True) for t in args.post_transcript], args.path)
    result = reconstruct(items, before, replayer)
    report = {
        "path": args.path,
        "before": before.isoformat() if before else None,
        "transcripts": [str(t) for t in args.transcript],
        "post_transcripts": [str(t) for t in args.post_transcript],
        "items_seen": [
            {"kind": item["kind"], "ts": item["ts"].isoformat(), "file": item["file"], "post": item["post"],
             **({"chars": len(item["content"]), "complete": item.get("complete")} if "content" in item else {}),
             **({"command": item["command"][:120]} if "command" in item else {})}
            for item in items
        ],
        **{key: value for key, value in result.items() if key != "content"},
    }
    content = result["content"]
    if content is not None:
        report["sha256"] = sha(content)
        report["chars"] = len(content)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(content)
        else:
            sys.stdout.write(content)
    text = json.dumps(report, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n")
    else:
        print(text, file=sys.stderr)
    return 0 if content is not None else 3


if __name__ == "__main__":
    raise SystemExit(main())

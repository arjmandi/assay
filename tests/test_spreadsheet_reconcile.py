"""The spreadsheet-reconcile pack (worlds/recipes/spreadsheet-reconcile): the
generator is deterministic and every quarter it makes can be closed, the
world refuses what its rules forbid and says why, the checker tells a correct
reconciliation from a wrong or an incomplete one, and a whole quarter runs
through the real CLI and daemon, with a resume in the middle, to a WIN the
checker confirms."""

from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import run_cli, stop_run

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "worlds" / "recipes" / "spreadsheet-reconcile"
SCORE = PACK / "checks" / "score.py"

sys.path.insert(0, str(PACK))
import reconcile  # noqa: E402

# Every pack's checker is checks/score.py: load this one by its path, under a
# name of its own, so another pack's checker never answers for it.
_spec = importlib.util.spec_from_file_location("spreadsheet_reconcile_score", SCORE)
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)


def _rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="") as handle:
        return {row["id"]: row for row in csv.DictReader(handle)}


def solution(workdir: Path, key: dict, month: int):
    """The answer key's actions for one month, the AUTO_MATCH last."""
    label = reconcile.month_label(month)
    bank = _rows(workdir / "bank.csv")
    for item in key["discrepancies"]:
        if item["month"] != label:
            continue
        kind, books = item["kind"], item["books"]
        if kind in ("transposed", "sign_error"):
            yield "FIX", {"book": books[0], "field": "amount", "value": bank[item["bank"]]["amount"]}
        elif kind == "date_typo":
            yield "FIX", {"book": books[0], "field": "date", "value": bank[item["bank"]]["date"]}
        elif kind == "duplicate":
            yield "DELETE", {"book": books[1]}
        elif kind in ("fee_unrecorded", "interest_unrecorded"):
            yield "RECORD", {"bank": item["bank"], "category": "fee" if kind == "fee_unrecorded" else "interest"}
        else:
            yield "FLAG", {"book": books[0], "reason": kind}
    yield "AUTO_MATCH", {}


def _solve(workdir: Path, seed: int) -> None:
    key = reconcile.truth(seed)
    for month in range(1, reconcile.MONTHS + 1):
        for action, params in solution(workdir, key, month):
            result = reconcile.act(workdir, action, params)
            assert result["status"] == "ok", (seed, action, params, result)


def _fresh(tmp_path: Path, seed: int, name: str = "world") -> Path:
    workdir = tmp_path / name
    workdir.mkdir()
    reconcile.setup(workdir, seed)
    return workdir


def test_the_generator_is_deterministic(tmp_path):
    first, again, other = _fresh(tmp_path, 11, "a"), _fresh(tmp_path, 11, "b"), _fresh(tmp_path, 12, "c")
    for name in ("bank.csv", "books.csv", "state.json"):
        assert (first / name).read_bytes() == (again / name).read_bytes()
    assert (first / "bank.csv").read_bytes() != (other / "bank.csv").read_bytes()
    assert reconcile.truth(11) == reconcile.truth(11)


def test_every_quarter_closes_and_scores_correct(tmp_path):
    kinds: set[str] = set()
    for seed in range(40):
        workdir = _fresh(tmp_path, seed, f"seed{seed}")
        key = reconcile.truth(seed)
        assert len(key["discrepancies"]) >= 3 * (reconcile.DISCREPANCIES_PER_MONTH - 1)
        kinds.update(item["kind"] for item in key["discrepancies"])
        _solve(workdir, seed)
        observed = reconcile.observe(workdir)
        assert observed["months_closed"] == 3 and observed["month"] == "done", seed
        report = score.score(seed, workdir)
        assert report["verdict"] == "correct", (seed, report)
    assert kinds == set(reconcile.KINDS)


def test_the_answer_key_never_enters_the_copy(tmp_path):
    workdir = _fresh(tmp_path, 5)
    assert sorted(path.name for path in workdir.iterdir()) == ["bank.csv", "books.csv", "state.json"]
    text = "".join(path.read_text() for path in workdir.iterdir())
    for word in ("transposed", "duplicate", "outstanding", "in_transit", "sign_error", "date_typo"):
        assert word not in text


def _first(rows: dict, **match) -> dict:
    return next(row for row in rows.values() if all(row[key] == value for key, value in match.items()))


def test_the_world_refuses_what_its_rules_forbid(tmp_path):
    workdir = _fresh(tmp_path, 7)
    bank, books = _rows(workdir / "bank.csv"), _rows(workdir / "books.csv")

    def refused(action: str, **params) -> str:
        result = reconcile.act(workdir, action, params)
        assert result["status"] == "refused", (action, params, result)
        return result["detail"]

    assert refused("MATCH", bank="B2-01", book="K2-01") == "B2-01 belongs to 2026-08, which opens when 2026-07 closes"
    # Seed 7's first transposed line: HARBOR, bank -321.10, books -231.10.
    assert refused("MATCH", bank="B1-04", book="K1-04") == "the amounts differ: bank -321.10, books -231.10"
    fee = _first(bank, description="MONTHLY SERVICE FEE")
    assert refused("RECORD", bank="B1-04", category="fee") == "B1-04 (CARD PURCHASE HARBOR PAPER CO) is not a bank fee line"
    assert fee["id"].startswith("B1-")
    assert refused("DELETE", book="K1-04") == "only a line entered twice can be deleted, and K1-04 has no twin"
    assert refused("FIX", book="K1-04", field="amount", value="-321.1") == "'-321.1' is not an amount such as -1234.50"
    assert refused("FIX", book="K1-04", field="date", value="2026-08-03") == "2026-08-03 is outside 2026-07"
    check = _first(books, ref="1044")
    assert refused("FLAG", book=check["id"], reason="outstanding_check") == (
        "only a check dated in the last 5 days of the month can be outstanding"
    )
    deposit = next(row for row in books.values() if row["ref"].startswith("D") and row["id"].startswith("K1-"))
    assert refused("FLAG", book=deposit["id"], reason="outstanding_check") == f"{deposit['id']} is not a check"
    assert refused("UNFLAG", book="K1-04") == "K1-04 is open, not flagged"
    assert refused("UNMATCH", bank="B1-04") == "B1-04 is open, not matched"
    # Nothing a refusal touched changed the copy.
    assert _rows(workdir / "bank.csv") == bank and _rows(workdir / "books.csv") == books


def test_unmatch_takes_a_recorded_line_back_out(tmp_path):
    workdir = _fresh(tmp_path, 7)
    key = reconcile.truth(7)
    fee_bank = next(bank for bank, category in key["records"].items() if category == "fee" and bank.startswith("B2-"))
    for action, params in solution(workdir, key, 1):
        assert reconcile.act(workdir, action, params)["status"] == "ok"
    recorded = reconcile.act(workdir, "RECORD", {"bank": fee_bank, "category": "fee"})
    assert recorded["status"] == "ok" and recorded["detail"].startswith(f"recorded {fee_bank} in the books as K2-80")
    assert reconcile.observe(workdir)["recorded"] == 1
    undone = reconcile.act(workdir, "UNMATCH", {"bank": fee_bank})
    assert undone == {"status": "ok", "detail": f"unmatched {fee_bank} and removed the recorded line K2-80"}
    assert "K2-80" not in _rows(workdir / "books.csv")
    assert reconcile.observe(workdir)["recorded"] == 0


def test_the_checker_tells_wrong_and_incomplete_from_correct(tmp_path):
    workdir = _fresh(tmp_path, 9)
    assert score.score(9, workdir)["verdict"] == "incomplete"
    _solve(workdir, 9)
    assert score.score(9, workdir)["verdict"] == "correct"
    # A baseline that edits the ledgers itself can pair lines wrongly or flag
    # without cause; the checker reads the same files and says so.
    swapped = tmp_path / "swapped"
    shutil.copytree(workdir, swapped)
    bank = _rows(swapped / "bank.csv")
    first, second = sorted(row_id for row_id, row in bank.items() if row["id"].startswith("B1-") and "CARD PURCHASE" in row["description"])[:2]
    bank[first]["match"], bank[second]["match"] = bank[second]["match"], bank[first]["match"]
    with (swapped / "bank.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=reconcile.BANK_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(bank.values())
    report = score.score(9, swapped)
    assert report["verdict"] == "wrong" and len(report["pairs"]["wrong"]) == 2
    flagged = tmp_path / "flagged"
    shutil.copytree(workdir, flagged)
    books = _rows(flagged / "books.csv")
    innocent = next(row for row in books.values() if row["status"] == "matched" and not row["flag"])
    innocent["flag"] = "outstanding_check"
    with (flagged / "books.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=reconcile.BOOKS_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(books.values())
    report = score.score(9, flagged)
    assert report["verdict"] == "wrong" and report["flags"]["wrong"] == [{"books": innocent["id"], "flag": "outstanding_check"}]


# --- a whole quarter through the real CLI and daemon ------------------------------


def _start(run: Path, *extra: str):
    return run_cli(
        run, "start", "reconcile1", "--adapter", f"{PACK / 'adapter.py'}:factory",
        "--registry", str(PACK / "registry.json"), "--seed", "3", *extra,
    )


def _act(run: Path, action: str, params: dict, predict: str):
    argv = ["act", action, "--params", json.dumps(params), "--predict", predict]
    if action == "DELETE":
        argv += ["--declare", "worst_case=a real line is lost", "--declare", "recovery=reset the month"]
    return run_cli(run, *argv)


def test_a_quarter_through_the_cli_with_a_resume(tmp_path):
    seed = 3
    run = tmp_path / "quarter"
    run.mkdir()
    workdir = run / ".assay" / "world"
    key = reconcile.truth(seed)
    try:
        started = _start(run, "--owner-token-file", str(tmp_path / "quarter.token"))
        assert started.returncode == 0, started.stderr
        assert "STATUS | reconcile1 | event 0 | level 1/3 | paid actions 0 | NOT_FINISHED" in started.stdout
        for name in ("bank_open", "books_open", "months_closed", "refusals", "difference"):
            assert run_cli(run, "state", "declare", name, "--path", name).returncode == 0
        # The destructive gate prices a DELETE before the world sees it: seed
        # 3 has a line entered twice in its first month.
        first_twins = key["duplicates"][0]
        assert first_twins[0].startswith("K1-")
        ungated = run_cli(run, "act", "DELETE", f"book={first_twins[1]}", "--predict", "change")
        assert ungated.returncode == 2 and "worst case and recovery" in ungated.stderr, ungated.stderr
        for month in range(1, reconcile.MONTHS + 1):
            steps = list(solution(workdir, key, month))
            for index, (action, params) in enumerate(steps):
                closing = index == len(steps) - 1
                if closing and month == reconcile.MONTHS:
                    predict = "win; level+1; ch months_closed = 3"
                elif closing:
                    predict = f"level+1; ch months_closed = {month}"
                else:
                    predict = "change; ch refusals delta = 0"
                acted = _act(run, action, params, predict)
                assert acted.returncode == 0, (action, params, acted.stderr)
                assert "RESULT | PREDICTED" in acted.stdout or "RESULT | LEVEL_COMPLETE" in acted.stdout or (
                    "RESULT | GAME_COMPLETE" in acted.stdout
                ), acted.stdout
            if month == 1:
                # A resume between months rebuilds the copy and replays the journal.
                before = (workdir / "books.csv").read_bytes(), (workdir / "bank.csv").read_bytes()
                assert run_cli(run, "stop").returncode == 0
                resumed = _start(run)
                assert resumed.returncode == 0, resumed.stderr
                assert f"RECOVERED | reconcile1 | local simulator | replayed {len(steps)} paid actions" in resumed.stdout
                assert ((workdir / "books.csv").read_bytes(), (workdir / "bank.csv").read_bytes()) == before
        status = run_cli(run, "status")
        assert "level 3/3" in status.stdout and "WIN" in status.stdout
        assert "AUDIT | CLEAN" in run_cli(run, "audit").stdout
        scored = subprocess.run([sys.executable, str(SCORE), "--run", str(run)], capture_output=True, text=True, timeout=60)
        assert scored.returncode == 0, scored.stdout + scored.stderr
        assert scored.stdout.startswith(f"SCORE | spreadsheet-reconcile | seed {seed} | correct")
    finally:
        stop_run(run)

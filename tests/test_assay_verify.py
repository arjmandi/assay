"""Tests for the standalone verify/assay_verify.py against verify/fixtures/.

`verify/assay_verify.py` imports nothing from `assay` — that is its whole
point (arjmandi/me#753) — so it is loaded here by file path, not by package
import, to keep that boundary honest rather than merely assumed. This test
file itself is free to import the kernel's `assay.integrity` for the parity
cross-check in TestKernelParity; that check is repo-internal QA, not
something the shipped verifier does.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = REPO_ROOT / "verify" / "fixtures"
VERIFY_SCRIPT = REPO_ROOT / "verify" / "assay_verify.py"

CASES = [
    "passing",
    "broken-contiguity",
    "broken-chain",
    "broken-anchor",
    "broken-gate",
    "broken-predict-grade",
]


def _load_assay_verify():
    spec = importlib.util.spec_from_file_location("assay_verify_standalone", VERIFY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assay_verify = _load_assay_verify()


def test_verify_script_imports_nothing_from_the_repo():
    """The one non-negotiable boundary: no `assay`/kernel import, ever."""
    source = VERIFY_SCRIPT.read_text()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import assay") or stripped.startswith("from assay"):
            pytest.fail(f"assay_verify.py imports the kernel: {stripped!r}")


def _fixture_anchor(case_dir: Path) -> Path | None:
    anchor_file = case_dir / "anchors.jsonl"
    return anchor_file if anchor_file.exists() else None


@pytest.mark.parametrize("case", CASES)
def test_fixture_matches_expected_verdict(case):
    case_dir = FIXTURES_DIR / case
    expected = json.loads((case_dir / "expected.json").read_text())
    report = assay_verify.audit(case_dir / "run", anchor_file=_fixture_anchor(case_dir))

    assert report["contiguous"] == expected["contiguous"]
    assert report["chain"] == expected["chain"]
    assert report["anchors"] == expected["anchors"]
    assert report["ungated"] == expected["ungated"]
    assert report["predict_grade_mismatches"] == expected["predict_grade_mismatches"]
    assert report["invalid_for_scoring"] == expected["invalid_for_scoring"]


def test_passing_fixture_is_the_only_clean_one():
    for case in CASES:
        case_dir = FIXTURES_DIR / case
        report = assay_verify.audit(case_dir / "run", anchor_file=_fixture_anchor(case_dir))
        if case == "passing":
            assert not report["invalid_for_scoring"], case
        else:
            assert report["invalid_for_scoring"], case


def test_expect_head_flag_matches_anchor_file_result():
    """--expect-head is the third-party path (a published bare pair, no
    anchor file needed); it must agree with the bundled anchors.jsonl."""
    for case in ("passing", "broken-anchor"):
        case_dir = FIXTURES_DIR / case
        anchor_entry = json.loads((case_dir / "anchors.jsonl").read_text().splitlines()[0])
        via_file = assay_verify.audit(case_dir / "run", anchor_file=case_dir / "anchors.jsonl")
        via_expect_head = assay_verify.audit(
            case_dir / "run",
            expect_head=(int(anchor_entry["event_id"]), str(anchor_entry["head"])),
        )
        assert via_file["anchors"] == via_expect_head["anchors"]
        assert via_file["invalid_for_scoring"] == via_expect_head["invalid_for_scoring"]


def test_cli_exit_code_and_json_output(tmp_path):
    import subprocess

    passing = FIXTURES_DIR / "passing"
    clean = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), str(passing / "run"), "--json"],
        capture_output=True,
        text=True,
    )
    assert clean.returncode == 0, clean.stderr
    report = json.loads(clean.stdout)
    assert report["invalid_for_scoring"] is False

    broken = FIXTURES_DIR / "broken-gate"
    dirty = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), str(broken / "run")],
        capture_output=True,
        text=True,
    )
    assert dirty.returncode == 1, dirty.stderr
    assert "INVALID FOR SCORING" in dirty.stdout
    assert "UNGATED events [2]" in dirty.stdout


def test_fixtures_are_reproducible_from_the_generator(tmp_path):
    """build.py is the source of truth; the committed fixtures must be
    exactly what it produces, byte for byte, or they have drifted."""
    sys.path.insert(0, str(FIXTURES_DIR))
    from build import build_all

    regenerated = tmp_path / "fixtures"
    build_all(regenerated)

    for case in CASES:
        for relative in ("expected.json", "run/.assay/events.jsonl", "run/.assay/chain.json"):
            committed = (FIXTURES_DIR / case / relative).read_text()
            fresh = (regenerated / case / relative).read_text()
            assert committed == fresh, f"{case}/{relative} has drifted from build.py"


class TestKernelParity:
    """Cross-check the standalone verifier against the kernel's own `assay
    audit` (src/assay/integrity.py) on every fixture — two independent
    implementations of the same spec agreeing is the trust claim
    (docs/LAYER1_ASSESSMENT.md's suggested acceptance test). This class is
    the only place in this file that imports the kernel."""

    @pytest.fixture(autouse=True)
    def _kernel(self):
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from assay import integrity
        from assay.core import RunPaths

        self.integrity = integrity
        self.RunPaths = RunPaths

    def _kernel_audit(self, case_dir: Path, tmp_path: Path, anchor_event_id_and_head=None):
        import shutil

        run_copy = tmp_path / "run"
        shutil.copytree(case_dir / "run", run_copy)
        paths = self.RunPaths(run_copy)
        if anchor_event_id_and_head is not None:
            import hashlib
            import os

            anchor_dir = tmp_path / "anchors"
            anchor_dir.mkdir()
            os.environ["ASSAY_ANCHOR_DIR"] = str(anchor_dir)
            try:
                digest = hashlib.sha256(str(paths.root.resolve()).encode()).hexdigest()[:24]
                event_id, head = anchor_event_id_and_head
                (anchor_dir / f"{digest}.jsonl").write_text(
                    json.dumps({"event_id": event_id, "head": head, "run": str(paths.root)}) + "\n"
                )
                return self.integrity.audit(paths)
            finally:
                os.environ.pop("ASSAY_ANCHOR_DIR", None)
        return self.integrity.audit(paths)

    @pytest.mark.parametrize(
        "case", ["passing", "broken-contiguity", "broken-chain", "broken-gate"]
    )
    def test_agrees_on_cases_without_anchors(self, case, tmp_path):
        case_dir = FIXTURES_DIR / case
        standalone = assay_verify.audit(case_dir / "run", anchor_file=None)
        kernel_report = self._kernel_audit(case_dir, tmp_path)

        assert standalone["contiguous"] == kernel_report["contiguous"]
        assert standalone["chain"] == kernel_report["chain"]
        assert standalone["ungated"] == kernel_report["ungated"]
        assert standalone["paid"] == kernel_report["paid"]
        assert standalone["events"] == kernel_report["events"]

    def test_agrees_on_the_anchor_check(self, tmp_path):
        case_dir = FIXTURES_DIR / "broken-anchor"
        anchor_entry = json.loads((case_dir / "anchors.jsonl").read_text().splitlines()[0])
        pair = (int(anchor_entry["event_id"]), str(anchor_entry["head"]))

        standalone = assay_verify.audit(case_dir / "run", expect_head=pair)
        kernel_report = self._kernel_audit(case_dir, tmp_path, anchor_event_id_and_head=pair)

        assert standalone["anchors"] == "DIVERGED"
        assert kernel_report["anchors"] == "DIVERGED"

    def test_predict_grade_check_is_the_standalone_tools_own_addition(self, tmp_path):
        """The one deliberate divergence: assay audit doesn't check
        predict/grade consistency, so it reports this fixture CLEAN while
        the standalone tool correctly flags it."""
        case_dir = FIXTURES_DIR / "broken-predict-grade"
        standalone = assay_verify.audit(case_dir / "run")
        kernel_report = self._kernel_audit(case_dir, tmp_path)

        assert standalone["invalid_for_scoring"] is True
        assert kernel_report["invalid_for_scoring"] is False

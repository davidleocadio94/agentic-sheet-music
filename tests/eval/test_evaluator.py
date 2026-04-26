"""Sanity tests for the evaluator. Spec: see eval-fixtures/ + builder/runner."""

from __future__ import annotations

from pathlib import Path

from agentic_sheet_music.eval.evaluator import evaluate

FIXTURES = Path(__file__).parent.parent.parent / "eval-fixtures"


def test_evaluator_perfect_on_self() -> None:
    """GT vs GT must score 100% on every fixture."""
    for gt in sorted(FIXTURES.rglob("ground-truth.musicxml")):
        result = evaluate(gt, gt, fixture_name=gt.parent.name)
        assert result.passed, (
            f"{gt.parent.name} self-eval is not perfect: "
            f"{result.matched_measures}/{result.total_measures} -- "
            f"{[m.diff_summary for m in result.measures if not m.match]}"
        )


def test_evaluator_catches_pitch_swap(tmp_path: Path) -> None:
    """Swap two notes — score must drop below 1."""
    gt = FIXTURES / "01-pitch" / "01-c-major-scale" / "ground-truth.musicxml"
    text = gt.read_text()
    # Swap C -> X (intentionally invalid step) on the very first note.
    bad = text.replace("<step>C</step>", "<step>X</step>", 1)
    bad_path = tmp_path / "bad.musicxml"
    bad_path.write_text(bad)
    result = evaluate(bad_path, gt, fixture_name="bad")
    assert not result.passed
    assert result.matched_measures < result.total_measures

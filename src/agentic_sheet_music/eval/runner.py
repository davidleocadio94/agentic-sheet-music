"""Eval runner: walk eval-fixtures/, run OMR on each PDF, score against GT.

A fixture directory looks like:

  eval-fixtures/01-pitch/01-c-major-scale/
    ground-truth.musicxml      ← human-authored, source of truth
    source.pdf                 ← generated from ground-truth via Verovio
                                  (rebuilt by `runner.refresh_pdfs()` if missing)
    candidate.musicxml         ← OMR output (regenerated each eval run)
    result.json                ← evaluator output (overwritten each run)

The runner gathers all results into an `EvalRunSummary` with the overall
score across the dataset.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agentic_sheet_music.eval.builder import GroundTruthBuildError, musicxml_to_pdf
from agentic_sheet_music.eval.evaluator import EvalResult, evaluate
from agentic_sheet_music.omr.gemini_omr import (
    GeminiOmrError,
    GeminiOmrNotConfigured,
    pdf_to_musicxml,
)


@dataclass(frozen=True)
class FixtureRun:
    fixture_name: str
    fixture_dir: Path
    result: EvalResult | None
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.result is not None and self.result.passed


@dataclass(frozen=True)
class EvalRunSummary:
    runs: tuple[FixtureRun, ...]
    total_fixtures: int
    passed_fixtures: int
    total_measures: int
    matched_measures: int
    model: str = ""
    notes: str = ""
    breakdown: dict = field(default_factory=dict)

    @property
    def score(self) -> float:
        if self.total_measures == 0:
            return 0.0
        return self.matched_measures / self.total_measures

    @property
    def perfect(self) -> bool:
        return (
            self.total_fixtures > 0
            and self.passed_fixtures == self.total_fixtures
        )


def discover_fixtures(root: Path) -> list[Path]:
    """Return every directory under `root` that contains a ground-truth.musicxml."""
    if not root.exists():
        return []
    out: list[Path] = []
    for gt in root.rglob("ground-truth.musicxml"):
        out.append(gt.parent)
    out.sort()
    return out


def refresh_pdfs(fixtures_root: Path, *, force: bool = False) -> None:
    """Regenerate source.pdf from ground-truth.musicxml for every fixture
    that's missing one (or all of them if force=True).
    """
    for d in discover_fixtures(fixtures_root):
        gt = d / "ground-truth.musicxml"
        pdf = d / "source.pdf"
        if pdf.exists() and not force:
            continue
        try:
            musicxml_to_pdf(gt, pdf)
        except GroundTruthBuildError as e:
            print(f"  [WARN] could not build {pdf}: {e}")


def run(
    fixtures_root: Path,
    *,
    only: list[str] | None = None,
    model: str = "gemini-3.1-pro-preview",
    notes: str = "",
) -> EvalRunSummary:
    """Run OMR + evaluator on every fixture under `fixtures_root`."""
    refresh_pdfs(fixtures_root)
    fixtures = discover_fixtures(fixtures_root)
    if only:
        fixtures = [d for d in fixtures if d.name in only or any(o in str(d) for o in only)]

    runs: list[FixtureRun] = []
    total_measures = 0
    matched_measures = 0
    passed = 0

    for fdir in fixtures:
        name = "/".join(fdir.relative_to(fixtures_root).parts)
        gt = fdir / "ground-truth.musicxml"
        pdf = fdir / "source.pdf"
        candidate = fdir / "candidate.musicxml"
        result_json = fdir / "result.json"

        if not pdf.exists():
            runs.append(FixtureRun(name, fdir, None, "source.pdf missing"))
            continue

        try:
            pdf_to_musicxml(pdf, candidate, model=model)
        except (GeminiOmrError, GeminiOmrNotConfigured) as e:
            runs.append(FixtureRun(name, fdir, None, f"OMR failed: {e}"))
            continue

        try:
            res = evaluate(candidate, gt, fixture_name=name)
        except Exception as e:  # noqa: BLE001
            runs.append(FixtureRun(name, fdir, None, f"evaluator failed: {e}"))
            continue

        result_json.write_text(_dump_result_json(res))

        runs.append(FixtureRun(name, fdir, res))
        total_measures += res.total_measures
        matched_measures += res.matched_measures
        if res.passed:
            passed += 1

    breakdown = {
        r.fixture_name: {
            "score": r.result.score if r.result else 0.0,
            "matched": r.result.matched_measures if r.result else 0,
            "total": r.result.total_measures if r.result else 0,
            "error": r.error,
        }
        for r in runs
    }

    return EvalRunSummary(
        runs=tuple(runs),
        total_fixtures=len(runs),
        passed_fixtures=passed,
        total_measures=total_measures,
        matched_measures=matched_measures,
        model=model,
        notes=notes,
        breakdown=breakdown,
    )


def _dump_result_json(res: EvalResult) -> str:
    """Serialise an EvalResult (incl. nested dataclasses) to JSON."""
    payload = {
        "fixture_name": res.fixture_name,
        "total_measures": res.total_measures,
        "matched_measures": res.matched_measures,
        "score": res.score,
        "passed": res.passed,
        "measures": [
            {
                "measure": m.measure,
                "match": m.match,
                "diff_summary": m.diff_summary,
                "expected": list(m.expected.events),
                "actual": list(m.actual.events) if m.actual else None,
            }
            for m in res.measures
        ],
    }
    return json.dumps(payload, indent=2, default=str)


def print_summary(summary: EvalRunSummary) -> None:
    print(f"\n=== eval summary ({summary.model}) ===")
    print(
        f"  {summary.passed_fixtures}/{summary.total_fixtures} fixtures pass | "
        f"{summary.matched_measures}/{summary.total_measures} measures match | "
        f"score {summary.score:.1%}"
    )
    if summary.notes:
        print(f"  notes: {summary.notes}")
    for r in summary.runs:
        if r.error:
            print(f"  [ERROR ] {r.fixture_name}: {r.error}")
            continue
        if r.passed:
            print(f"  [PASS  ] {r.fixture_name} ({r.result.matched_measures}/{r.result.total_measures})")
        else:
            print(
                f"  [FAIL  ] {r.fixture_name} "
                f"({r.result.matched_measures}/{r.result.total_measures})"
            )
            for m in r.result.measures:
                if not m.match:
                    print(f"      m.{m.measure}: {m.diff_summary}")

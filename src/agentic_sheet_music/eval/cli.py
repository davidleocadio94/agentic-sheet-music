"""`eval` CLI: run the eval suite or just one fixture.

Usage:
  uv run eval                          # run everything
  uv run eval --only 01-pitch          # filter
  uv run eval --refresh-pdfs           # rebuild source.pdfs from GT
  uv run eval --json results.json      # also dump machine-readable summary
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentic_sheet_music.eval.runner import (
    EvalRunSummary,
    print_summary,
    refresh_pdfs,
    run,
)


DEFAULT_FIXTURES_ROOT = Path("eval-fixtures")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval", description="Run the OMR eval suite.")
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURES_ROOT,
        help=f"Path to fixtures root (default: {DEFAULT_FIXTURES_ROOT})",
    )
    parser.add_argument(
        "--only",
        action="append",
        help="Filter to a fixture name or path substring; repeat to allow several.",
    )
    parser.add_argument(
        "--model",
        default="gemini-3.1-pro-preview",
        help="OMR model string.",
    )
    parser.add_argument(
        "--refresh-pdfs",
        action="store_true",
        help="Rebuild every source.pdf from its ground-truth.musicxml, then exit.",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Write a JSON summary to this path.",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Free-form note added to the run summary (logged in JSON).",
    )
    args = parser.parse_args(argv)

    if args.refresh_pdfs:
        refresh_pdfs(args.fixtures, force=True)
        print("refreshed all source.pdf files")
        return 0

    summary = run(args.fixtures, only=args.only, model=args.model, notes=args.notes)
    print_summary(summary)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(_summary_to_json(summary))

    return 0 if summary.perfect else 1


def _summary_to_json(s: EvalRunSummary) -> str:
    payload = {
        "model": s.model,
        "notes": s.notes,
        "score": s.score,
        "perfect": s.perfect,
        "total_fixtures": s.total_fixtures,
        "passed_fixtures": s.passed_fixtures,
        "total_measures": s.total_measures,
        "matched_measures": s.matched_measures,
        "breakdown": s.breakdown,
    }
    return json.dumps(payload, indent=2, default=str)


if __name__ == "__main__":
    sys.exit(main())

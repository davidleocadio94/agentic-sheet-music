"""CLI entrypoint: `analyze <score-path> [flags]`.

Wired into `[project.scripts]` as `analyze`. PDF inputs are transcribed via
Gemini Vision (the only OMR engine in this project as of 2026-04-25).
"""

from __future__ import annotations

import argparse
import datetime
import sys
from dataclasses import dataclass
from pathlib import Path

from agentic_sheet_music.harmony import build_analysis
from agentic_sheet_music.harmony.cadence import find_cadences
from agentic_sheet_music.harmony.chord_extraction import (
    ChordExtractionError,
    extract_chords,
)
from agentic_sheet_music.harmony.key_detection import KeyDetectionError, detect_keys
from agentic_sheet_music.harmony.roman import assign_roman
from agentic_sheet_music.omr.ingest import IngestError, ingest, ingest_all
from agentic_sheet_music.player.synth import AudioRenderError, render_audio
from agentic_sheet_music.types import HarmonyAnalysis, Score


@dataclass(frozen=True)
class _StageOutput:
    score: Score
    analysis: HarmonyAnalysis


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="analyze", description="Analyze a score.")
    parser.add_argument("score", type=Path, help="Path to score (.pdf, .musicxml, .mid, ...)")
    parser.add_argument("--keys", action="store_true", help="Run key detection.")
    parser.add_argument(
        "--window", type=int, default=4, help="Key-detection window in measures (default 4)."
    )
    parser.add_argument("--chords", action="store_true", help="Run chord extraction.")
    parser.add_argument(
        "--chords-per-measure",
        type=int,
        default=1,
        help="Max chords per measure for reduction (default 1).",
    )
    parser.add_argument(
        "--roman",
        action="store_true",
        help="Run roman-numeral analysis (implies --keys and --chords).",
    )
    parser.add_argument(
        "--cadences",
        action="store_true",
        help="Detect cadences (implies --roman).",
    )
    parser.add_argument(
        "--annotate",
        action="store_true",
        help="Write an annotated PDF copy (implies all analysis stages).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path for --annotate. Default: outputs/<stem>/annotated-<timestamp>.pdf",
    )
    parser.add_argument(
        "--audio",
        action="store_true",
        help=(
            "Render MIDI (always) and WAV (if fluidsynth + SoundFont available). "
            "Writes to outputs/<stem>/audio/."
        ),
    )
    parser.add_argument(
        "--playalong",
        action="store_true",
        help="Build a static playalong site (implies --annotate and --audio).",
    )
    parser.add_argument(
        "--vendor-cache",
        type=Path,
        default=None,
        help="Override the vendor cache location for --playalong.",
    )
    args = parser.parse_args(argv)

    if not args.score.exists():
        print(f"error: {args.score} does not exist", file=sys.stderr)
        return 2

    try:
        scores = ingest_all(args.score)
    except IngestError as e:
        print(f"ingest failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    outputs: list[_StageOutput] = []
    for i, sc in enumerate(scores):
        if len(scores) > 1:
            print(f"\n=== movement {i + 1} ===")
        try:
            out = _analyze_one(sc, args, verbose=(i == 0))
        except (KeyDetectionError, ChordExtractionError) as e:
            print(f"analysis failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        outputs.append(out)

    annotated_pdf: Path | None = None
    midi_paths: list[Path] = []

    if args.annotate or args.playalong:
        rc, annotated_pdf = _run_annotate(args, outputs)
        if rc != 0:
            return rc
    if args.audio or args.playalong:
        rc, midi_paths = _run_audio(args, outputs)
        if rc != 0:
            return rc
    if args.playalong:
        rc = _run_playalong(args, outputs, annotated_pdf, midi_paths)
        if rc != 0:
            return rc
    return 0


def _analyze_one(
    score: Score, args: argparse.Namespace, *, verbose: bool
) -> _StageOutput:
    if verbose:
        print(f"loaded: {score.meta.title or args.score.name}")
        print(f"  composer: {score.meta.composer or '-'}")
        print(
            f"  time: {score.meta.time_signature or '-'}   "
            f"key: {score.meta.key_signature or '-'}"
        )
        print(f"  parts: {len(score.parts)}")
        for p in score.parts:
            print(f"    - {p.name} ({p.instrument or 'unspecified'})")
        print(f"  source_confidence: {score.source_confidence:.2f}")

    need_keys = (
        args.keys or args.roman or args.cadences or args.annotate or args.audio or args.playalong
    )
    need_chords = (
        args.chords or args.roman or args.cadences or args.annotate or args.audio or args.playalong
    )
    need_rn = args.roman or args.cadences or args.annotate or args.audio or args.playalong
    need_cadences = args.cadences or args.annotate or args.audio or args.playalong

    regions = detect_keys(score, window_measures=args.window) if need_keys else ()
    if verbose and args.keys:
        print(f"\nkey regions ({len(regions)}):")
        for r in regions:
            conf = f"{r.confidence:.2f}" if r.confidence is not None else "-"
            print(f"  m.{r.start_measure:>3}-{r.end_measure:<3}  {r.key:<12}  conf={conf}")

    chords = (
        extract_chords(score, max_chords_per_measure=args.chords_per_measure)
        if need_chords
        else ()
    )
    if verbose and args.chords and not args.roman:
        print(f"\nchord events ({len(chords)}):")
        for e in chords[:40]:
            print(f"  m.{e.measure:>3} b{e.beat:<4}  {e.label:<6}  {' '.join(e.pitches)}")
        if len(chords) > 40:
            print(f"  ... ({len(chords) - 40} more)")

    rn_events: tuple = ()
    ambiguities: tuple = ()
    if need_rn:
        rn_events, ambiguities = assign_roman(chords, regions)
        if verbose:
            print(f"\nroman numerals ({len(rn_events)}; {len(ambiguities)} ambiguities):")
            for e in rn_events[:60]:
                print(
                    f"  m.{e.measure:>3} b{e.beat:<4}  {e.numeral:<10}  "
                    f"[{e.key}]  {e.rationale}"
                )
            if len(rn_events) > 60:
                print(f"  ... ({len(rn_events) - 60} more)")

    cads: tuple = find_cadences(rn_events) if need_cadences else ()
    if verbose and args.cadences:
        print(f"\ncadences ({len(cads)}):")
        for c in cads:
            span = (
                f"m.{c.start_measure}"
                if c.start_measure == c.end_measure
                else f"m.{c.start_measure}-{c.end_measure}"
            )
            print(f"  {span:<10}  {c.kind:<4}  {c.rationale}")

    analysis = build_analysis(
        score=score,
        key_regions=regions,
        chords=chords,
        roman_numerals=rn_events,
        cadences=cads,
        ambiguities=ambiguities,
    )
    return _StageOutput(score=score, analysis=analysis)


def _run_annotate(
    args: argparse.Namespace, outputs: list[_StageOutput]
) -> tuple[int, Path | None]:
    print(
        "annotate: not wired for the Gemini OMR path yet — the previous "
        "facsimile overlay needed Audiveris's .omr book for coordinates. "
        "Skipping. The eval loop is currently the primary target.",
        file=sys.stderr,
    )
    return 0, None


def _default_output_path(pdf_path: Path) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path("outputs") / pdf_path.stem
    return out_dir / f"annotated-{stamp}.pdf"


def _run_audio(
    args: argparse.Namespace, outputs: list[_StageOutput]
) -> tuple[int, list[Path]]:
    base = Path("outputs") / args.score.stem / "audio"
    midi_paths: list[Path] = []
    for i, out in enumerate(outputs):
        mvt_dir = base if len(outputs) == 1 else base / f"mvt{i + 1}"
        sections = tuple((c.start_measure, c.end_measure) for c in out.analysis.cadences)
        try:
            result = render_audio(out.score, mvt_dir, sections=sections)
        except AudioRenderError as e:
            print(f"audio render failed: {e}", file=sys.stderr)
            return 1, midi_paths
        midi_paths.append(result.midi)
        wav = (
            f", WAV: {result.full_wav.name}"
            if result.full_wav
            else " (no WAV — install fluidsynth)"
        )
        print(f"audio: MIDI {result.midi}, {len(result.section_wavs)} clips{wav}")
    return 0, midi_paths


def _run_playalong(
    args: argparse.Namespace,
    outputs: list[_StageOutput],
    annotated_pdf: Path | None,
    midi_paths: list[Path],
) -> int:
    print(
        "playalong: not wired for the Gemini OMR path yet — depends on "
        "annotate which is also pending. Coming back to this once eval "
        "loop hits 100%.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""CLI entrypoint: `analyze <score-path> [flags]`.

Wired into `[project.scripts]` as `analyze`. For multi-movement PDFs (e.g. a
milonga with guitar 1 and guitar 2 on separate page groups), the per-stage
printouts show movement 1 only; `--annotate` always covers every movement.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from dataclasses import dataclass
from pathlib import Path

from agentic_sheet_music.annotate.pdf import (
    AnnotationOutputError,
    annotate_pdf_all_movements,
)
from agentic_sheet_music.harmony import build_analysis
from agentic_sheet_music.harmony.cadence import find_cadences
from agentic_sheet_music.harmony.chord_extraction import (
    ChordExtractionError,
    extract_chords,
)
from agentic_sheet_music.harmony.key_detection import KeyDetectionError, detect_keys
from agentic_sheet_music.harmony.roman import assign_roman
from agentic_sheet_music.omr.ingest import IngestError, ingest, ingest_all
from agentic_sheet_music.omr.vlm_autocorrect import (
    AutoCorrectionError,
    autocorrect_score,
)
from agentic_sheet_music.omr.vlm_verifier import (
    VerifierError,
    VerifierNotConfigured,
    verify_score,
)
from agentic_sheet_music.player.synth import AudioRenderError, render_audio
from agentic_sheet_music.playalong.build import (
    PlayalongBuildError,
    build_playalong,
)
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
        help=(
            "Write an annotated PDF copy covering every movement. "
            "Source must be a PDF with an .omr sidecar from Audiveris."
        ),
    )
    parser.add_argument(
        "--omr-book",
        type=Path,
        default=None,
        help="Path to the Audiveris .omr book file for --annotate.",
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
            "Render MIDI (always) and WAV (if fluidsynth + SoundFont available) "
            "for every movement. Writes to outputs/<stem>/audio/mvt<N>/."
        ),
    )
    parser.add_argument(
        "--playalong",
        action="store_true",
        help=(
            "Build a static playalong site (implies --annotate and --audio). "
            "Writes to outputs/<stem>/playalong/. Run `python -m "
            "agentic_sheet_music.playalong.fetch_vendor` once to download the "
            "browser MIDI engine + SoundFont."
        ),
    )
    parser.add_argument(
        "--vendor-cache",
        type=Path,
        default=None,
        help="Override the vendor cache location for --playalong.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Run a vision-LLM (Gemini 3.1 Pro) verification pass over the OMR "
            "output, page by page, against the source PDF. Reports transcription "
            "errors. Costs ~$0.30/page in API fees."
        ),
    )
    parser.add_argument(
        "--verify-model",
        default="gemini-3.1-pro-preview",
        help="Model string for --verify (default gemini-3.1-pro-preview).",
    )
    parser.add_argument(
        "--verify-max-pages",
        type=int,
        default=None,
        help="Cap pages sent to the verifier (default: every page).",
    )
    parser.add_argument(
        "--auto-correct",
        action="store_true",
        help=(
            "After --verify, apply Gemini's structured fixes to the MusicXML "
            "and use the corrected version for downstream stages (--audio, "
            "--annotate, --playalong). Implies --verify."
        ),
    )
    parser.add_argument(
        "--correction-confidence",
        type=float,
        default=0.95,
        help="Min Gemini confidence (0-1) to auto-apply a fix (default 0.95).",
    )
    args = parser.parse_args(argv)

    if not args.score.exists():
        print(f"error: {args.score} does not exist", file=sys.stderr)
        return 2

    multi_movement = (
        args.annotate or args.audio or args.playalong or args.verify or args.auto_correct
    )
    try:
        scores = ingest_all(args.score) if multi_movement else (ingest(args.score),)
    except IngestError as e:
        print(f"ingest failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if args.auto_correct:
        rc, scores = _run_auto_correct(args, scores)
        if rc != 0:
            return rc

    # Run the analysis stages on every movement. Verbose per-stage output
    # prints only the first movement to keep the CLI readable.
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
    if args.verify:
        rc = _run_verify(args, outputs)
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

    want = (
        args.keys
        or args.chords
        or args.roman
        or args.cadences
        or args.annotate
        or args.audio
        or args.playalong
    )
    need_keys = want
    need_chords = (
        args.chords or args.roman or args.cadences or args.annotate or args.audio or args.playalong
    )
    need_rn = args.roman or args.cadences or args.annotate or args.audio or args.playalong
    need_cadences = args.cadences or args.annotate or args.audio or args.playalong
    del want

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
            print(
                f"\nroman numerals ({len(rn_events)}; {len(ambiguities)} ambiguities):"
            )
            for e in rn_events[:60]:
                print(
                    f"  m.{e.measure:>3} b{e.beat:<4}  {e.numeral:<10}  "
                    f"[{e.key}]  {e.rationale}"
                )
            if len(rn_events) > 60:
                print(f"  ... ({len(rn_events) - 60} more)")
            if ambiguities:
                print(f"\nambiguities ({len(ambiguities)}):")
                for a in ambiguities[:10]:
                    print(f"  m.{a.measure}: {a.readings}  — {a.rationale}")

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
    if args.score.suffix.lower() != ".pdf":
        print("--annotate requires a PDF source", file=sys.stderr)
        return 1, None
    omr_book = args.omr_book or _locate_omr_book(args.score)
    if omr_book is None or not omr_book.exists():
        print(
            "Could not locate .omr book. Pass --omr-book <path>.",
            file=sys.stderr,
        )
        return 1, None
    output = args.output or _default_output_path(args.score)
    analyses = tuple(o.analysis for o in outputs)
    try:
        result = annotate_pdf_all_movements(args.score, omr_book, analyses, output)
    except AnnotationOutputError as e:
        print(f"annotation error: {e}", file=sys.stderr)
        return 1, None
    print(f"\nannotated PDF: {result}")
    return 0, result


def _locate_omr_book(pdf_path: Path) -> Path | None:
    """Look for a <pdf-stem>.omr next to the source, or in /tmp/audiveris-smoke/."""
    stem = pdf_path.stem
    for candidate in (
        pdf_path.with_suffix(".omr"),
        Path("/tmp/audiveris-smoke") / f"{stem}.omr",
    ):
        if candidate.exists():
            return candidate
    return None


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
        mvt_dir = base / f"mvt{i + 1}"
        sections = tuple(
            (c.start_measure, c.end_measure) for c in out.analysis.cadences
        )
        try:
            result = render_audio(out.score, mvt_dir, sections=sections)
        except AudioRenderError as e:
            print(f"audio render failed for movement {i + 1}: {e}", file=sys.stderr)
            return 1, midi_paths
        midi_paths.append(result.midi)
        wav = (
            f", WAV: {result.full_wav.name}"
            if result.full_wav
            else " (no WAV — install fluidsynth)"
        )
        print(
            f"audio movement {i + 1}: MIDI {result.midi}, "
            f"{len(result.section_wavs)} section clips{wav}"
        )
    return 0, midi_paths


def _run_playalong(
    args: argparse.Namespace,
    outputs: list[_StageOutput],
    annotated_pdf: Path | None,
    midi_paths: list[Path],
) -> int:
    if annotated_pdf is None or not midi_paths:
        print(
            "internal: playalong needs annotated_pdf + midi_paths", file=sys.stderr
        )
        return 1
    omr_book = args.omr_book or _locate_omr_book(args.score)
    if omr_book is None:
        print("Could not locate .omr book for playalong.", file=sys.stderr)
        return 1
    out_dir = Path("outputs") / args.score.stem / "playalong"
    # Clear stale contents to keep the build deterministic.
    if out_dir.exists():
        import shutil as _shutil

        _shutil.rmtree(out_dir)
    try:
        index = build_playalong(
            source_pdf=args.score,
            annotated_pdf=annotated_pdf,
            omr_book=omr_book,
            analyses=tuple(o.analysis for o in outputs),
            midi_paths=tuple(midi_paths),
            output_dir=out_dir,
            vendor_cache=args.vendor_cache,
        )
    except PlayalongBuildError as e:
        print(f"playalong build failed: {e}", file=sys.stderr)
        return 1
    print(f"\nplayalong site: {index}")
    print("  open that file in Chrome or Firefox.")
    print(
        f"  for Safari: `bash {out_dir / 'serve.sh'}` "
        "then browse http://localhost:8080/"
    )
    return 0


def _run_auto_correct(
    args: argparse.Namespace, scores: tuple[Score, ...]
) -> tuple[int, tuple[Score, ...]]:
    """Run Gemini auto-correction on each movement's MusicXML and return new
    Scores pointing at the corrected files.
    """
    from dataclasses import replace as dc_replace

    if args.score.suffix.lower() != ".pdf":
        print("--auto-correct currently requires a PDF source", file=sys.stderr)
        return 1, scores
    omr_book = args.omr_book or _locate_omr_book(args.score)
    if omr_book is None or not omr_book.exists():
        print("Could not locate .omr book for --auto-correct.", file=sys.stderr)
        return 1, scores

    out_dir = Path("outputs") / args.score.stem / "corrected-xml"
    out_dir.mkdir(parents=True, exist_ok=True)

    new_scores: list[Score] = []
    for i, sc in enumerate(scores):
        candidate = Path(sc.musicxml_path)
        out_xml = out_dir / f"mvt{i + 1}.corrected.xml"
        if out_xml.exists():
            out_xml.unlink()
        print(f"\n=== auto-correcting movement {i + 1} ===")
        try:
            result = autocorrect_score(
                source_pdf=args.score,
                candidate_xml=candidate,
                omr_book=omr_book,
                movement=i + 1,
                auto_apply=True,
                min_confidence=args.correction_confidence,
                output_xml=out_xml,
                model=args.verify_model,
                max_pages=args.verify_max_pages,
            )
        except (AutoCorrectionError, VerifierError) as e:
            print(f"auto-correction failed: {e}", file=sys.stderr)
            return 1, scores
        except VerifierNotConfigured as e:
            print(f"verifier not configured: {e}", file=sys.stderr)
            return 1, scores

        print(
            f"  applied {len(result.applied_fixes)} fixes, "
            f"skipped {len(result.skipped_fixes)}"
        )
        for f in result.applied_fixes[:30]:
            print(f"    [APPLIED] m.{f.measure} {f.op}")
        if len(result.applied_fixes) > 30:
            print(f"    ... ({len(result.applied_fixes) - 30} more)")
        new_scores.append(dc_replace(sc, musicxml_path=result.corrected_xml or candidate))

    return 0, tuple(new_scores)


def _run_verify(args: argparse.Namespace, outputs: list[_StageOutput]) -> int:
    if args.score.suffix.lower() != ".pdf":
        print("--verify currently requires a PDF source", file=sys.stderr)
        return 1
    omr_book = args.omr_book or _locate_omr_book(args.score)
    if omr_book is None or not omr_book.exists():
        print("Could not locate .omr book for --verify.", file=sys.stderr)
        return 1

    for i, out in enumerate(outputs):
        print(f"\n=== verifying movement {i + 1} ===")
        xml_path = Path(out.score.musicxml_path)
        try:
            report = verify_score(
                source_pdf=args.score,
                candidate_xml=xml_path,
                omr_book=omr_book,
                movement=i + 1,
                model=args.verify_model,
                max_pages=args.verify_max_pages,
            )
        except VerifierNotConfigured as e:
            print(f"verifier not configured: {e}", file=sys.stderr)
            return 1
        except VerifierError as e:
            print(f"verifier failed: {e}", file=sys.stderr)
            return 1

        print(
            f"  model: {report.model}   "
            f"{len(report.pages)} pages, {report.total_disagreements} disagreements"
        )
        for p in report.pages:
            header = f"  page {p.page_index + 1}: conf={p.overall_confidence:.2f}"
            if p.observed_key_signature or p.observed_time_signature:
                header += (
                    f"   observed: key={p.observed_key_signature!r}  "
                    f"time={p.observed_time_signature!r}"
                )
            print(header)
            for d in p.disagreements:
                fix = f"   fix: {d.suggested_fix}" if d.suggested_fix else ""
                print(
                    f"    m.{d.measure:>3} conf={d.confidence:.2f}: {d.issue}{fix}"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())

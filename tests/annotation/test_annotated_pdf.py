"""Tests for annotated-pdf. Spec: specs/feature-annotated-pdf.md."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

MILONGA_PDF = Path("/Users/davidvillarreal/Documents/Music/ClassicalGuitar/milonga.pdf")
MILONGA_OMR = Path("/tmp/audiveris-smoke/milonga.omr")


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_parses_measure_boxes_from_omr() -> None:
    from agentic_sheet_music.annotate.pdf import parse_measure_boxes

    if not MILONGA_OMR.exists() or not MILONGA_PDF.exists():
        pytest.skip(f"milonga .omr fixture not available at {MILONGA_OMR}")

    boxes = parse_measure_boxes(MILONGA_OMR, MILONGA_PDF, movement=1)
    assert len(boxes) > 0, "expected at least one measure box"
    # Milonga movement 1 has 57 measures (15 + 21 + 21 across three sheets).
    assert len(boxes) == 57, f"expected 57 measures in movement 1, got {len(boxes)}"
    # Measure 1 should start near the top-left of page 1.
    m1 = boxes[1]
    assert m1.page_index == 0
    assert m1.x0 < m1.x1
    assert m1.y0 < m1.y1


def test_respects_movement_boundary() -> None:
    from agentic_sheet_music.annotate.pdf import parse_measure_boxes

    if not MILONGA_OMR.exists() or not MILONGA_PDF.exists():
        pytest.skip("milonga .omr fixture not available")

    m1_boxes = parse_measure_boxes(MILONGA_OMR, MILONGA_PDF, movement=1)
    m2_boxes = parse_measure_boxes(MILONGA_OMR, MILONGA_PDF, movement=2)

    # Movement 1 = sheets 1-3, movement 2 = sheets 4-6. Both movements should
    # have measure numbering restarting at 1.
    assert 1 in m1_boxes
    assert 1 in m2_boxes
    assert m1_boxes[1].page_index == 0  # first page
    assert m2_boxes[1].page_index == 3  # sheets start over on page 4 (0-indexed: 3)


@pytest.mark.omr_binary
def test_annotate_milonga_all_movements(tmp_path: Path) -> None:
    """Correctness: the annotated PDF must cover *all* movements in the source PDF,
    not just movement 1. The Cardoso milonga has guitar 1 (pp.1-3) and guitar 2
    (pp.4-6) — both must receive roman-numeral markup.
    """
    from agentic_sheet_music.annotate.pdf import annotate_pdf_all_movements
    from agentic_sheet_music.harmony import build_analysis
    from agentic_sheet_music.harmony.cadence import find_cadences
    from agentic_sheet_music.harmony.chord_extraction import extract_chords
    from agentic_sheet_music.harmony.key_detection import detect_keys
    from agentic_sheet_music.harmony.roman import assign_roman
    from agentic_sheet_music.omr.ingest import ingest_all
    from agentic_sheet_music.omr.pdf_to_musicxml import which_audiveris

    if which_audiveris() is None or not MILONGA_PDF.exists() or not MILONGA_OMR.exists():
        pytest.skip("Audiveris / milonga fixtures not available")

    original_hash = _sha256(MILONGA_PDF)
    scores = ingest_all(MILONGA_PDF)
    # Milonga has 2 movements (guitar 1 / guitar 2).
    assert len(scores) == 2, f"expected 2 movements, got {len(scores)}"

    analyses = []
    for sc in scores:
        chords = extract_chords(sc, max_chords_per_measure=1)
        regions = detect_keys(sc)
        rns, ambig = assign_roman(chords, regions)
        cads = find_cadences(rns)
        analyses.append(
            build_analysis(
                score=sc,
                key_regions=regions,
                chords=chords,
                roman_numerals=rns,
                cadences=cads,
                ambiguities=ambig,
            )
        )

    out = tmp_path / "annotated-all.pdf"
    result = annotate_pdf_all_movements(
        MILONGA_PDF, MILONGA_OMR, tuple(analyses), out
    )

    assert result.exists()
    # Open the result and verify pages from both movements have our annotations.
    import pymupdf

    doc = pymupdf.open(result)
    try:
        # Every page should have >= 1 roman-numeral-like annotation glyph.
        # Roman numerals use characters like "i", "V", "I", "7" — rather than
        # matching on content (fragile), just assert the total text on pages
        # 4-6 grew relative to the source: the source pages have title+notes,
        # the annotated pages also have our RN/key/cadence overlay text.
        source_doc = pymupdf.open(MILONGA_PDF)
        source_text_lens = [len(source_doc[i].get_text()) for i in range(len(source_doc))]
        annotated_text_lens = [len(doc[i].get_text()) for i in range(len(doc))]
        source_doc.close()

        # Pages 0-2 = mvt1 (already confirmed works); pages 3-5 = mvt2 (the new case).
        assert len(annotated_text_lens) == len(source_text_lens) == 6
        for page_idx in (3, 4, 5):
            assert annotated_text_lens[page_idx] > source_text_lens[page_idx], (
                f"page {page_idx + 1} (mvt2) has no more text than the source — "
                f"annotations missing"
            )
    finally:
        doc.close()
    # Source untouched.
    assert _sha256(MILONGA_PDF) == original_hash, "source PDF was modified!"


@pytest.mark.omr_binary
def test_annotate_milonga_end_to_end(tmp_path: Path) -> None:
    """Full pipeline: PDF → OMR → harmony analysis → annotated PDF."""
    from agentic_sheet_music.annotate.pdf import annotate_pdf
    from agentic_sheet_music.harmony import build_analysis
    from agentic_sheet_music.harmony.cadence import find_cadences
    from agentic_sheet_music.harmony.chord_extraction import extract_chords
    from agentic_sheet_music.harmony.key_detection import detect_keys
    from agentic_sheet_music.harmony.roman import assign_roman
    from agentic_sheet_music.omr.ingest import ingest
    from agentic_sheet_music.omr.pdf_to_musicxml import which_audiveris

    if which_audiveris() is None or not MILONGA_PDF.exists() or not MILONGA_OMR.exists():
        pytest.skip("Audiveris / milonga fixtures not available")

    original_hash = _sha256(MILONGA_PDF)

    score = ingest(MILONGA_PDF)
    chords = extract_chords(score, max_chords_per_measure=1)
    regions = detect_keys(score)
    rn_events, ambiguities = assign_roman(chords, regions)
    cadences = find_cadences(rn_events)
    analysis = build_analysis(
        score=score,
        key_regions=regions,
        chords=chords,
        roman_numerals=rn_events,
        cadences=cadences,
        ambiguities=ambiguities,
    )

    out = tmp_path / "annotated.pdf"
    result = annotate_pdf(MILONGA_PDF, MILONGA_OMR, analysis, out)

    assert result == out
    assert out.exists()
    assert out.stat().st_size > 10_000, f"annotated PDF suspiciously small: {out.stat().st_size} bytes"
    # Source must be untouched.
    assert _sha256(MILONGA_PDF) == original_hash, "source PDF was modified!"


def test_refuses_to_overwrite_source(tmp_path: Path) -> None:
    from agentic_sheet_music.annotate.pdf import AnnotationOutputError, annotate_pdf
    from agentic_sheet_music.types import (
        HarmonyAnalysis,
        Part,
        Score,
        ScoreMeta,
    )

    fake_pdf = tmp_path / "dummy.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n")  # not a real PDF, but we expect to fail before reading it
    fake_omr = tmp_path / "dummy.omr"
    fake_omr.write_bytes(b"fake")
    analysis = HarmonyAnalysis(
        score=Score(
            musicxml_path=tmp_path / "fake.xml",
            meta=ScoreMeta(title="", composer=None, time_signature=None, key_signature=None),
            parts=(Part(name="p", instrument=None),),
            source_confidence=1.0,
        ),
        key_regions=(),
        chords=(),
        roman_numerals=(),
        cadences=(),
        ambiguities=(),
    )
    with pytest.raises(AnnotationOutputError):
        annotate_pdf(fake_pdf, fake_omr, analysis, fake_pdf)


def test_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    from agentic_sheet_music.annotate.pdf import AnnotationOutputError, annotate_pdf
    from agentic_sheet_music.types import (
        HarmonyAnalysis,
        Part,
        Score,
        ScoreMeta,
    )

    fake_pdf = tmp_path / "src.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n")
    fake_omr = tmp_path / "src.omr"
    fake_omr.write_bytes(b"fake")
    existing_out = tmp_path / "out.pdf"
    existing_out.write_bytes(b"existing")

    analysis = HarmonyAnalysis(
        score=Score(
            musicxml_path=tmp_path / "fake.xml",
            meta=ScoreMeta(title="", composer=None, time_signature=None, key_signature=None),
            parts=(),
            source_confidence=1.0,
        ),
        key_regions=(),
        chords=(),
        roman_numerals=(),
        cadences=(),
        ambiguities=(),
    )
    with pytest.raises(AnnotationOutputError):
        annotate_pdf(fake_pdf, fake_omr, analysis, existing_out)

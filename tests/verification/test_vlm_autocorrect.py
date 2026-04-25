"""Tests for vlm-autocorrect. Spec: specs/feature-vlm-autocorrect.md."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

MILONGA_PDF = Path("/Users/davidvillarreal/Documents/Music/ClassicalGuitar/milonga.pdf")
MILONGA_OMR = Path("/tmp/audiveris-smoke/milonga.omr")
MILONGA_XML = Path("/tmp/audiveris-smoke/extracted/milonga.mvt1.xml")


# A tiny synthetic MusicXML used by every unit test that mutates XML.
def _write_synthetic(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>P</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>8</divisions>
        <key><fifths>0</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note>
        <pitch><step>E</step><octave>5</octave></pitch>
        <duration>1</duration><voice>1</voice><type>16th</type>
        <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>
        <notations><tuplet number="1" type="start"/></notations>
      </note>
      <note>
        <pitch><step>F</step><octave>5</octave></pitch>
        <duration>1</duration><voice>1</voice><type>16th</type>
        <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>
        <notations><tuplet number="1" type="stop"/></notations>
      </note>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>12</duration><voice>2</voice><type>quarter</type>
        <dot/>
      </note>
      <note>
        <pitch><step>A</step><octave>3</octave></pitch>
        <duration>8</duration><voice>2</voice><type>quarter</type>
      </note>
    </measure>
  </part>
</score-partwise>
"""
    )


def test_apply_remove_tuplet_strips_time_modification(tmp_path: Path) -> None:
    from agentic_sheet_music.omr.vlm_autocorrect import _apply_remove_tuplet

    src = tmp_path / "in.xml"
    _write_synthetic(src)
    tree = ET.parse(src)
    root = tree.getroot()

    ok = _apply_remove_tuplet(root, measure=1, note_pitch="E5")
    assert ok, "remove_tuplet should match the E5 note"
    # E5 should have neither <time-modification> nor <notations><tuplet>.
    e5 = next(
        n
        for n in root.iter("note")
        if (p := n.find("pitch")) is not None
        and p.findtext("step") == "E"
        and p.findtext("octave") == "5"
    )
    assert e5.find("time-modification") is None
    assert e5.find("notations/tuplet") is None


def test_apply_change_pitch_updates_step_octave_alter(tmp_path: Path) -> None:
    from agentic_sheet_music.omr.vlm_autocorrect import _apply_change_pitch

    src = tmp_path / "in.xml"
    _write_synthetic(src)
    tree = ET.parse(src)
    root = tree.getroot()

    ok = _apply_change_pitch(root, measure=1, from_pitch="A3", to_pitch="A4")
    assert ok
    a4 = next(
        n
        for n in root.iter("note")
        if (p := n.find("pitch")) is not None
        and p.findtext("step") == "A"
        and p.findtext("octave") == "4"
    )
    assert a4 is not None


def test_apply_remove_dot_changes_duration(tmp_path: Path) -> None:
    from agentic_sheet_music.omr.vlm_autocorrect import _apply_remove_dot

    src = tmp_path / "in.xml"
    _write_synthetic(src)
    tree = ET.parse(src)
    root = tree.getroot()

    # The synthetic C4 note is dotted (dur 12 with divisions 8 = dotted-quarter).
    ok = _apply_remove_dot(root, measure=1, note_pitch="C4")
    assert ok
    c4 = next(
        n
        for n in root.iter("note")
        if (p := n.find("pitch")) is not None
        and p.findtext("step") == "C"
        and p.findtext("octave") == "4"
    )
    assert c4.find("dot") is None
    assert c4.findtext("duration") == "8"  # back to plain quarter


def test_apply_change_time_signature(tmp_path: Path) -> None:
    from agentic_sheet_music.omr.vlm_autocorrect import _apply_change_time_signature

    src = tmp_path / "in.xml"
    _write_synthetic(src)
    tree = ET.parse(src)
    root = tree.getroot()

    ok = _apply_change_time_signature(root, beats=2, beat_type=4)
    assert ok
    time_el = root.find(".//time")
    assert time_el is not None
    assert time_el.findtext("beats") == "2"
    assert time_el.findtext("beat-type") == "4"


def test_skip_when_pitch_not_found(tmp_path: Path) -> None:
    from agentic_sheet_music.omr.vlm_autocorrect import _apply_change_pitch

    src = tmp_path / "in.xml"
    _write_synthetic(src)
    tree = ET.parse(src)
    root = tree.getroot()

    ok = _apply_change_pitch(root, measure=1, from_pitch="B7", to_pitch="C8")
    assert ok is False


def test_no_apply_when_auto_apply_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """auto_apply=False produces a report but no corrected file."""
    from agentic_sheet_music.omr import vlm_autocorrect
    from agentic_sheet_music.types import (
        MeasureDisagreement,
        PageVerification,
        ScoreVerification,
    )

    # Stub the verifier to return a single fixable disagreement.
    fake_verification = ScoreVerification(
        pages=(
            PageVerification(
                page_index=0,
                overall_confidence=0.95,
                observed_key_signature=None,
                observed_time_signature=None,
                disagreements=(
                    MeasureDisagreement(
                        measure=1, issue="x", suggested_fix=None, confidence=0.99
                    ),
                ),
            ),
        ),
        model="stub",
        total_disagreements=1,
    )
    monkeypatch.setattr(
        vlm_autocorrect, "_verify_with_structured_fixes",
        lambda **kwargs: (fake_verification, []),
    )

    src = tmp_path / "in.xml"
    _write_synthetic(src)
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n")
    fake_omr = tmp_path / "fake.omr"
    fake_omr.write_bytes(b"")

    result = vlm_autocorrect.autocorrect_score(
        source_pdf=fake_pdf,
        candidate_xml=src,
        omr_book=fake_omr,
        auto_apply=False,
    )
    assert result.corrected_xml is None
    assert result.verification is fake_verification


def test_refuses_overwriting_source(tmp_path: Path) -> None:
    from agentic_sheet_music.omr.vlm_autocorrect import (
        AutoCorrectionError,
        autocorrect_score,
    )

    src = tmp_path / "in.xml"
    _write_synthetic(src)
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n")
    fake_omr = tmp_path / "fake.omr"
    fake_omr.write_bytes(b"")

    with pytest.raises(AutoCorrectionError):
        autocorrect_score(
            source_pdf=fake_pdf,
            candidate_xml=src,
            omr_book=fake_omr,
            auto_apply=True,
            output_xml=src,  # same as input
        )


@pytest.mark.omr_binary
def test_milonga_corrected_has_fewer_tuplets(tmp_path: Path) -> None:
    """Correctness: the corrected XML must have strictly fewer triplet
    markers than the original. The Cardoso milonga is in 2/4 with no
    triplets; every <time-modification> in Audiveris's output is a
    fingering-circle-misread. Removing them measurably improves rhythm.
    """
    from agentic_sheet_music.omr.pdf_to_musicxml import which_audiveris
    from agentic_sheet_music.omr.vlm_autocorrect import autocorrect_score

    if which_audiveris() is None or not MILONGA_PDF.exists() or not MILONGA_OMR.exists():
        pytest.skip("Audiveris / milonga fixtures not available")
    if not MILONGA_XML.exists():
        pytest.skip("milonga candidate XML not available")

    out = tmp_path / "milonga.corrected.xml"
    result = autocorrect_score(
        source_pdf=MILONGA_PDF,
        candidate_xml=MILONGA_XML,
        omr_book=MILONGA_OMR,
        movement=1,
        auto_apply=True,
        output_xml=out,
        # Limit to page 1 for test speed; real pipeline uses all pages.
    )
    assert result.corrected_xml == out
    assert out.exists()

    before = ET.parse(MILONGA_XML).getroot()
    after = ET.parse(out).getroot()
    before_tm = sum(1 for _ in before.iter("time-modification"))
    after_tm = sum(1 for _ in after.iter("time-modification"))
    assert after_tm < before_tm, (
        f"expected strictly fewer <time-modification> in corrected XML; "
        f"before={before_tm}, after={after_tm}, applied={len(result.applied_fixes)}"
    )

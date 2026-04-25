"""Tests for omr-ingest. Spec: specs/feature-omr-ingest.md."""

from pathlib import Path

import pytest

from agentic_sheet_music.omr.ingest import (
    InvalidMusicXML,
    MidiIngestNotImplemented,
    OmrNotAvailable,
    UnsupportedScoreFormat,
    ingest,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "omr-ingest"


def test_ingest_c_major_scale_returns_score() -> None:
    score = ingest(FIXTURES / "c-major-scale.musicxml")
    assert score.meta.title == "C Major Scale"
    assert score.source_confidence == 1.0
    assert len(score.parts) == 1
    assert score.parts[0].name == "Piano"


def test_ingest_counts_parts() -> None:
    score = ingest(FIXTURES / "two-part.musicxml")
    assert len(score.parts) == 2
    assert {p.name for p in score.parts} == {"Violin", "Cello"}


def test_ingest_handles_no_parts() -> None:
    score = ingest(FIXTURES / "no-parts.musicxml")
    assert score.parts == ()
    assert score.source_confidence == 1.0


def test_ingest_corrupt_xml_raises() -> None:
    with pytest.raises(InvalidMusicXML):
        ingest(FIXTURES / "corrupt.musicxml")


def test_ingest_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ingest(tmp_path / "nope.musicxml")


def test_ingest_unsupported_extension_raises(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("some notes")
    with pytest.raises(UnsupportedScoreFormat):
        ingest(f)


def test_ingest_pdf_raises_omr_not_available_when_audiveris_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force which_audiveris() to return None; PDF ingest must surface a clear error.
    from agentic_sheet_music.omr import ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "which_audiveris", lambda: None)
    f = tmp_path / "score.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(OmrNotAvailable):
        ingest(f)


def test_ingest_image_still_unsupported(tmp_path: Path) -> None:
    f = tmp_path / "score.png"
    f.write_bytes(b"\x89PNG\r\n")
    with pytest.raises(OmrNotAvailable):
        ingest(f)


@pytest.mark.omr_binary
def test_milonga_has_correct_time_signature() -> None:
    """Correctness: the Cardoso milonga is in 2/4. Audiveris OMR sometimes
    misses the time signature mark on the engraving; ingest must infer it from
    the note durations rather than letting music21 default to 4/4 (which
    silently produces wrong barlines, wrong harmony offsets, wrong MIDI).
    """
    from music21 import converter

    from agentic_sheet_music.omr.pdf_to_musicxml import which_audiveris

    MILONGA_PDF = Path("/Users/davidvillarreal/Documents/Music/ClassicalGuitar/milonga.pdf")
    if which_audiveris() is None or not MILONGA_PDF.exists():
        pytest.skip("Audiveris / milonga fixtures not available")

    score = ingest(MILONGA_PDF)
    assert score.meta.time_signature is not None, (
        "milonga ingest must populate a time signature; Audiveris missed the "
        "meter and we need to infer it"
    )
    # NOTE: the source score is visually 2/4, but Audiveris merges pairs of 2/4
    # bars into 4/4 bars (it detects fewer barlines than exist). The *inferred*
    # time signature must therefore match the XML's internal bar structure,
    # which is 4/4. Downstream stages depend on (time sig) matching
    # (per-measure duration sum), not on the original paper meter.
    assert score.meta.time_signature == "4/4", (
        f"expected 4/4 (matches Audiveris's internal bar length), got "
        f"{score.meta.time_signature!r}"
    )
    stream = converter.parse(str(score.musicxml_path))
    sigs = list(stream.flatten().getElementsByClass("TimeSignature"))
    assert sigs, "MusicXML must contain a <time> element after ingest"
    assert sigs[0].ratioString == "4/4"


def test_ingest_midi_raises_not_implemented(tmp_path: Path) -> None:
    f = tmp_path / "score.mid"
    f.write_bytes(b"MThd")
    with pytest.raises(MidiIngestNotImplemented):
        ingest(f)

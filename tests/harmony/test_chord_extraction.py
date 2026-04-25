"""Tests for harmony-chord-extraction. Spec: specs/feature-harmony-chord-extraction.md."""

from pathlib import Path

import pytest

from agentic_sheet_music.harmony.chord_extraction import (
    ChordExtractionError,
    extract_chords,
)
from agentic_sheet_music.omr.ingest import ingest
from agentic_sheet_music.omr.pdf_to_musicxml import which_audiveris

FIXTURES = Path(__file__).parent.parent / "fixtures" / "harmony-chord-extraction"
FIXTURES_KEY = Path(__file__).parent.parent / "fixtures" / "harmony-key-detection"
MILONGA_PDF = Path("/Users/davidvillarreal/Documents/Music/ClassicalGuitar/milonga.pdf")


def _pitch_class_set(pitches: tuple[str, ...]) -> set[str]:
    """Strip octave numbers — 'F4' -> 'F', 'B-4' -> 'B-'."""
    out: set[str] = set()
    for p in pitches:
        # Drop trailing digits only.
        i = len(p)
        while i > 0 and p[i - 1].isdigit():
            i -= 1
        out.add(p[:i])
    return out


def test_block_chords_extract_cleanly() -> None:
    score = ingest(FIXTURES / "block-chords.musicxml")
    events = extract_chords(score)
    assert len(events) == 4
    labels = [e.label for e in events]
    assert labels == ["F", "Bb", "C", "F"], labels


def test_arpeggiated_reduces_to_block_progression() -> None:
    """The critical test: arpeggiated F-Bb-C-F must carry the same per-measure harmony."""
    arp = extract_chords(ingest(FIXTURES / "arpeggiated.musicxml"), max_chords_per_measure=1)
    block = extract_chords(ingest(FIXTURES / "block-chords.musicxml"), max_chords_per_measure=1)

    # At max=1 the reducer picks the single strongest chord per measure;
    # an arpeggio should yield the same per-measure harmony as a block chord.
    assert len(arp) == len(block)
    for i, (a, b) in enumerate(zip(arp, block, strict=True)):
        assert a.measure == b.measure
        assert _pitch_class_set(a.pitches) == _pitch_class_set(b.pitches), (
            f"measure {a.measure}: arpeggio {_pitch_class_set(a.pitches)} != "
            f"block {_pitch_class_set(b.pitches)}"
        )


def test_two_chords_per_measure() -> None:
    score = ingest(FIXTURES / "two-chords-per-measure.musicxml")
    events = extract_chords(score, max_chords_per_measure=2)
    # 2 measures × 2 chords = 4 events minimum; allow the reducer to merge if it
    # decides a measure is really one harmony, but not for this unambiguous fixture.
    assert len(events) == 4, (
        f"expected 4 events for Dm-Gm | A-Dm, got {len(events)}: "
        f"{[(e.measure, e.beat, e.label) for e in events]}"
    )


def test_skips_empty_measures() -> None:
    score = ingest(FIXTURES / "empty-measure.musicxml")
    events = extract_chords(score)
    measures_with_chords = {e.measure for e in events}
    assert 2 not in measures_with_chords, (
        f"measure 2 is all rests but got chord events at {measures_with_chords}"
    )
    assert measures_with_chords >= {1, 3}


def test_raises_on_empty_score() -> None:
    # Reuse the key-detection empty fixture (all rests).
    score = ingest(FIXTURES_KEY / "empty.musicxml")
    with pytest.raises(ChordExtractionError):
        extract_chords(score)


def test_invalid_max_chords_raises() -> None:
    score = ingest(FIXTURES / "block-chords.musicxml")
    with pytest.raises(ValueError):
        extract_chords(score, max_chords_per_measure=0)


@pytest.mark.omr_binary
def test_milonga_extracts_many_chords() -> None:
    """Real-world smoke test on the Cardoso milonga PDF.

    This stage doesn't do non-chord-tone filtering yet, so labels are frequently
    '?' on real repertoire (the 4-note stacks include passing tones and other
    non-chord tones that obscure the underlying triad). Label quality is a
    downstream concern — we only assert here that the pipeline produces a
    reasonable number of events without crashing.
    """
    if which_audiveris() is None:
        pytest.skip("Audiveris not installed")
    if not MILONGA_PDF.exists():
        pytest.skip(f"sample PDF missing at {MILONGA_PDF}")

    score = ingest(MILONGA_PDF)
    events = extract_chords(score, max_chords_per_measure=2)
    assert len(events) >= 20, f"only extracted {len(events)} chords from the milonga"
    # Basic integrity: every event must have at least one pitch.
    assert all(e.pitches for e in events)
    # We expect at least some events to label cleanly (triads without NCTs).
    recognizable = sum(1 for e in events if e.label != "?")
    assert recognizable > 0, "no chord event got a recognizable label at all"

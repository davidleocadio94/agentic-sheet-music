"""Tests for harmony-roman. Spec: specs/feature-harmony-roman.md."""

from pathlib import Path

import pytest

from agentic_sheet_music.harmony.chord_extraction import extract_chords
from agentic_sheet_music.harmony.key_detection import detect_keys
from agentic_sheet_music.harmony.roman import assign_roman
from agentic_sheet_music.omr.ingest import ingest
from tests._compat import which_audiveris
from agentic_sheet_music.types import KeyRegion

FIXTURES = Path(__file__).parent.parent / "fixtures" / "harmony-roman"
MILONGA_PDF = Path("/Users/davidvillarreal/Documents/Music/ClassicalGuitar/milonga.pdf")


def test_c_major_I_IV_V_I() -> None:
    score = ingest(FIXTURES / "c-major-I-IV-V-I.musicxml")
    chords = extract_chords(score, max_chords_per_measure=1)
    regions = (KeyRegion(start_measure=1, end_measure=4, key="C major", confidence=1.0),)
    events, _ = assign_roman(chords, regions)
    assert [e.numeral for e in events] == ["I", "IV", "V", "I"]
    assert all(e.key == "C major" for e in events)


def test_d_minor_with_V7() -> None:
    score = ingest(FIXTURES / "d-minor-i-iv-V7-i.musicxml")
    chords = extract_chords(score, max_chords_per_measure=1)
    regions = (KeyRegion(start_measure=1, end_measure=4, key="D minor", confidence=1.0),)
    events, _ = assign_roman(chords, regions)
    assert [e.numeral for e in events] == ["i", "iv", "V7", "i"]


def test_ignores_passing_tones() -> None:
    score = ingest(FIXTURES / "c-major-with-passing.musicxml")
    chords = extract_chords(score, max_chords_per_measure=1)
    regions = (KeyRegion(start_measure=1, end_measure=2, key="C major", confidence=1.0),)
    events, _ = assign_roman(chords, regions)
    # Both measures should reduce to I (the D and F are passing tones).
    assert [e.numeral for e in events] == ["I", "I"], [
        (e.measure, e.numeral, e.rationale) for e in events
    ]


def test_secondary_dominant_resolves() -> None:
    score = ingest(FIXTURES / "secondary-dominant.musicxml")
    chords = extract_chords(score, max_chords_per_measure=1)
    regions = (KeyRegion(start_measure=1, end_measure=4, key="C major", confidence=1.0),)
    events, _ = assign_roman(chords, regions)
    # Progression: I  V7/ii  ii  V7 — last chord is plain V7 (G-B-D-F resolves
    # nowhere after it in this fixture, so no secondary-dominant triggering).
    assert events[0].numeral == "I"
    assert events[1].numeral == "V7/ii", (
        f"expected V7/ii at m.2, got {events[1].numeral}; rationale={events[1].rationale}"
    )
    assert events[2].numeral == "ii"
    assert events[3].numeral == "V7"


def test_ambiguity_recorded_for_chromatic() -> None:
    score = ingest(FIXTURES / "ambiguous-chromatic.musicxml")
    chords = extract_chords(score, max_chords_per_measure=1)
    regions = (KeyRegion(start_measure=1, end_measure=2, key="C major", confidence=0.3),)
    _, ambiguities = assign_roman(chords, regions)
    # Both measures have 4 chromatic pitches — at least one should be ambiguous.
    assert len(ambiguities) >= 1, "expected at least one ambiguity on chromatic fixture"


def test_requires_key_regions() -> None:
    with pytest.raises(ValueError):
        assign_roman((), ())


@pytest.mark.omr_binary
def test_milonga_rn_coverage() -> None:
    """Real-world smoke: roman-numeral pass on the Cardoso milonga PDF."""
    if which_audiveris() is None:
        pytest.skip("Audiveris not installed")
    if not MILONGA_PDF.exists():
        pytest.skip(f"sample PDF missing at {MILONGA_PDF}")

    score = ingest(MILONGA_PDF)
    chords = extract_chords(score, max_chords_per_measure=1)
    regions = detect_keys(score)
    events, ambiguities = assign_roman(chords, regions)

    assert len(events) >= 40
    recognizable = sum(1 for e in events if e.numeral != "?")
    # NCT-aware RN labeling should land > 50% of events with a real numeral.
    assert recognizable / len(events) >= 0.5, (
        f"only {recognizable}/{len(events)} events got a roman numeral; "
        f"first few: {[(e.measure, e.numeral, e.rationale) for e in events[:10]]}"
    )

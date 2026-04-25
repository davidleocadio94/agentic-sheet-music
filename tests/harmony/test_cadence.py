"""Tests for harmony-cadence. Spec: specs/feature-harmony-cadence.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_sheet_music.types import RomanEvent

MILONGA_PDF = Path("/Users/davidvillarreal/Documents/Music/ClassicalGuitar/milonga.pdf")


def _rn(
    measure: int, numeral: str, key: str = "C major", beat: float = 1.0
) -> RomanEvent:
    return RomanEvent(
        measure=measure,
        beat=beat,
        numeral=numeral,
        key=key,
        rationale="",
    )


def test_pac_on_root_position_V_to_I() -> None:
    from agentic_sheet_music.harmony.cadence import find_cadences

    events = (
        _rn(1, "I"),
        _rn(2, "IV"),
        _rn(3, "V"),
        _rn(4, "I"),
    )
    cads = find_cadences(events)
    assert len(cads) == 1, cads
    c = cads[0]
    assert c.kind == "PAC"
    assert c.start_measure == 3
    assert c.end_measure == 4


def test_iac_when_inverted_V() -> None:
    from agentic_sheet_music.harmony.cadence import find_cadences

    events = (
        _rn(1, "I"),
        _rn(2, "V6"),
        _rn(3, "I"),
    )
    cads = find_cadences(events)
    assert len(cads) == 1
    assert cads[0].kind == "IAC"


def test_hc_on_V_at_end() -> None:
    from agentic_sheet_music.harmony.cadence import find_cadences

    events = (
        _rn(1, "I"),
        _rn(2, "IV"),
        _rn(3, "V"),
    )
    cads = find_cadences(events)
    assert any(c.kind == "HC" for c in cads), cads


def test_deceptive_cadence() -> None:
    from agentic_sheet_music.harmony.cadence import find_cadences

    events = (
        _rn(1, "I"),
        _rn(2, "IV"),
        _rn(3, "V7"),
        _rn(4, "vi"),
    )
    cads = find_cadences(events)
    assert any(c.kind == "DC" for c in cads), cads


def test_plagal_when_no_authentic() -> None:
    from agentic_sheet_music.harmony.cadence import find_cadences

    events = (
        _rn(1, "I"),
        _rn(2, "IV"),
        _rn(3, "I"),
    )
    cads = find_cadences(events)
    assert any(c.kind == "PC" for c in cads), cads


def test_phrygian_half_in_minor() -> None:
    from agentic_sheet_music.harmony.cadence import find_cadences

    events = (
        _rn(1, "i", key="D minor"),
        _rn(2, "iv", key="D minor"),
        _rn(3, "V", key="D minor"),
    )
    cads = find_cadences(events)
    assert any(c.kind == "PhC" for c in cads), cads


def test_does_not_cross_key_regions() -> None:
    from agentic_sheet_music.harmony.cadence import find_cadences

    events = (
        _rn(3, "V", key="A minor"),
        _rn(4, "I", key="C major"),  # different key — not a cadence
    )
    cads = find_cadences(events)
    assert cads == ()


def test_empty_rn_list_returns_empty() -> None:
    from agentic_sheet_music.harmony.cadence import find_cadences

    assert find_cadences(()) == ()


def test_question_mark_breaks_pattern() -> None:
    from agentic_sheet_music.harmony.cadence import find_cadences

    events = (
        _rn(1, "I"),
        _rn(2, "V"),
        _rn(3, "?"),
        _rn(4, "I"),
    )
    cads = find_cadences(events)
    # Neither V→? nor ?→I is a cadence. The piece-ends-on-V rule doesn't fire
    # because V is not the last event.
    assert all(c.kind != "PAC" and c.kind != "IAC" for c in cads), cads


@pytest.mark.omr_binary
def test_milonga_finds_cadences() -> None:
    """Real-world smoke: the Cardoso milonga should have several cadences."""
    from agentic_sheet_music.harmony.cadence import find_cadences
    from agentic_sheet_music.harmony.chord_extraction import extract_chords
    from agentic_sheet_music.harmony.key_detection import detect_keys
    from agentic_sheet_music.harmony.roman import assign_roman
    from agentic_sheet_music.omr.ingest import ingest
    from agentic_sheet_music.omr.pdf_to_musicxml import which_audiveris

    if which_audiveris() is None:
        pytest.skip("Audiveris not installed")
    if not MILONGA_PDF.exists():
        pytest.skip(f"sample PDF missing at {MILONGA_PDF}")

    score = ingest(MILONGA_PDF)
    chords = extract_chords(score, max_chords_per_measure=1)
    regions = detect_keys(score)
    rn_events, _ = assign_roman(chords, regions)
    cads = find_cadences(rn_events)

    assert len(cads) >= 3, f"expected >= 3 cadences, got {len(cads)}: {cads}"
    # At least one authentic cadence on V7->i in D minor (the piece's home key).
    assert any(
        c.kind in {"PAC", "IAC"} and "D minor" in c.rationale for c in cads
    ), f"expected at least one authentic cadence in D minor; got {[(c.kind, c.rationale) for c in cads]}"

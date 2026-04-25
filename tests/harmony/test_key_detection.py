"""Tests for harmony-key-detection. Spec: specs/feature-harmony-key-detection.md."""

from pathlib import Path

import pytest

from agentic_sheet_music.harmony.key_detection import KeyDetectionError, detect_keys
from agentic_sheet_music.omr.ingest import ingest

FIXTURES_OMR = Path(__file__).parent.parent / "fixtures" / "omr-ingest"
FIXTURES_KEY = Path(__file__).parent.parent / "fixtures" / "harmony-key-detection"


def _load(path: Path):
    return ingest(path)


def test_detects_c_major_single_region() -> None:
    score = _load(FIXTURES_OMR / "c-major-scale.musicxml")
    regions = detect_keys(score)

    assert len(regions) == 1
    assert regions[0].key.lower().startswith("c major")
    assert regions[0].confidence is not None
    assert regions[0].confidence > 0.9


def test_detects_a_minor() -> None:
    score = _load(FIXTURES_KEY / "a-minor-phrase.musicxml")
    regions = detect_keys(score)
    keys = [r.key.lower() for r in regions]
    # Accept any region that identifies an A-tonic minor reading; the piece is
    # intentionally unambiguous harmonic-minor A.
    assert any("a minor" in k for k in keys), f"expected A minor in {keys}"


def test_detects_modulation_c_to_g() -> None:
    score = _load(FIXTURES_KEY / "c-to-g-modulation.musicxml")
    regions = detect_keys(score, window_measures=4)

    # We expect at least two regions (C region then G region), no overlaps,
    # full coverage 1..8.
    assert len(regions) >= 2
    keys_lower = [r.key.lower() for r in regions]
    assert any("c major" in k for k in keys_lower)
    assert any("g major" in k for k in keys_lower)

    # Coverage + no overlap.
    covered: set[int] = set()
    prev_end = 0
    for r in regions:
        assert r.start_measure == prev_end + 1 or prev_end == 0
        assert r.end_measure >= r.start_measure
        for m in range(r.start_measure, r.end_measure + 1):
            assert m not in covered, f"measure {m} covered twice"
            covered.add(m)
        prev_end = r.end_measure
    assert covered == set(range(1, 9))


def test_low_confidence_on_ambiguous() -> None:
    score = _load(FIXTURES_KEY / "ambiguous.musicxml")
    regions = detect_keys(score)
    # The piece is fully chromatic; confidence should be noticeably below a
    # tonal piece's. We don't pin a hard threshold (KS correlations for
    # 16-pitch chromatic fragments sit around 0.3–0.5 depending on spelling),
    # but it must be lower than the unambiguous C major fixture.
    ref = detect_keys(_load(FIXTURES_OMR / "c-major-scale.musicxml"))
    assert regions[0].confidence is not None
    assert ref[0].confidence is not None
    assert regions[0].confidence < ref[0].confidence


def test_raises_on_empty_score() -> None:
    score = _load(FIXTURES_KEY / "empty.musicxml")
    with pytest.raises(KeyDetectionError):
        detect_keys(score)


@pytest.mark.parametrize(
    "fixture",
    [
        FIXTURES_OMR / "c-major-scale.musicxml",
        FIXTURES_KEY / "a-minor-phrase.musicxml",
        FIXTURES_KEY / "c-to-g-modulation.musicxml",
    ],
)
def test_regions_cover_all_measures_without_gaps(fixture: Path) -> None:
    score = _load(fixture)
    regions = detect_keys(score)
    assert regions, "expected at least one region"
    prev_end = regions[0].start_measure - 1
    for r in regions:
        assert r.start_measure == prev_end + 1
        assert r.end_measure >= r.start_measure
        prev_end = r.end_measure


# ---------------------------------------------------------------------------
# Correctness tests — these assert specific musical facts, not just "produced output".
# See .claude/rules/correctness.md.


MILONGA_PDF = Path("/Users/davidvillarreal/Documents/Music/ClassicalGuitar/milonga.pdf")


@pytest.mark.omr_binary
def test_milonga_is_d_minor_throughout() -> None:
    """The Cardoso milonga is in D minor. KS alone gets this wrong on the opening;
    the detector must use the MusicXML key signature as a prior.
    """
    from agentic_sheet_music.omr.ingest import ingest
    from agentic_sheet_music.omr.pdf_to_musicxml import which_audiveris

    if which_audiveris() is None or not MILONGA_PDF.exists():
        pytest.skip("Audiveris / milonga fixtures not available")

    score = ingest(MILONGA_PDF)
    regions = detect_keys(score)

    # The piece's key signature is 1 flat. Every region must therefore be in a
    # 1-flat-compatible key: D minor (tonic) or F major (relative major).
    # No B- major, no G minor, no A minor, no C major on this piece.
    allowed = {"d minor", "f major"}
    bad = [r for r in regions if r.key.lower() not in allowed]
    assert not bad, (
        f"milonga has a 1-flat key signature; expected all regions in "
        f"{{D minor, F major}}, got: {[(r.start_measure, r.end_measure, r.key) for r in regions]}. "
        f"KS alone mislabels the opening — the detector must use the declared "
        f"key signature as a prior."
    )

    # And at least the bulk of the piece must be labeled D minor (the real tonic).
    d_minor_measures = sum(
        r.end_measure - r.start_measure + 1 for r in regions if r.key.lower() == "d minor"
    )
    total = sum(r.end_measure - r.start_measure + 1 for r in regions)
    assert d_minor_measures / total >= 0.7, (
        f"only {d_minor_measures}/{total} measures labeled D minor; "
        f"a D-minor milonga should be D-minor-dominant."
    )

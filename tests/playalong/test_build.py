"""Tests for playalong. Spec: specs/feature-playalong.md."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

MILONGA_PDF = Path("/Users/davidvillarreal/Documents/Music/ClassicalGuitar/milonga.pdf")
MILONGA_OMR = Path("/tmp/audiveris-smoke/milonga.omr")


@pytest.fixture
def _stub_vendor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point vendor asset resolution at a pre-populated cache so tests don't
    hit the network. Each test gets an empty cache with placeholder files.
    """
    cache = tmp_path / "vendor-cache"
    cache.mkdir()
    (cache / "spessasynth_lib.js").write_text("// placeholder library\n")
    (cache / "spessasynth_core.js").write_text("// placeholder core\n")
    (cache / "spessasynth_processor.js").write_text("// placeholder processor\n")
    (cache / "piano.sf3").write_bytes(b"\x00" * 2048)
    return cache


def test_measure_timings_monotonic(tmp_path: Path, _stub_vendor: Path) -> None:
    """Given a simple 4-measure fixture, onset_seconds should strictly increase."""
    from agentic_sheet_music.harmony import build_analysis
    from agentic_sheet_music.omr.ingest import ingest
    from agentic_sheet_music.player.synth import render_audio
    from agentic_sheet_music.playalong.build import _measure_onsets_seconds

    fixture = (
        Path(__file__).parent.parent
        / "fixtures"
        / "harmony-chord-extraction"
        / "block-chords.musicxml"
    )
    score = ingest(fixture)
    render = render_audio(score, tmp_path)

    # Synthetic measure numbers 1..4 in score order.
    onsets = _measure_onsets_seconds(render.midi, list(range(1, 5)))
    assert len(onsets) == 4
    prev = -1.0
    for m, t in onsets.items():
        assert t > prev, f"measure {m} onset {t} not greater than previous {prev}"
        prev = t


def test_refuses_missing_omr_book(tmp_path: Path, _stub_vendor: Path) -> None:
    from agentic_sheet_music.playalong.build import (
        PlayalongBuildError,
        build_playalong,
    )

    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(PlayalongBuildError):
        build_playalong(
            source_pdf=fake_pdf,
            annotated_pdf=fake_pdf,
            omr_book=tmp_path / "missing.omr",
            analyses=(),
            midi_paths=(),
            output_dir=tmp_path / "out",
            vendor_cache=_stub_vendor,
        )


@pytest.mark.omr_binary
def test_build_playalong_for_milonga(tmp_path: Path, _stub_vendor: Path) -> None:
    """Full pipeline: build a playalong site for the milonga, covering both movements."""
    from agentic_sheet_music.annotate.pdf import annotate_pdf_all_movements
    from agentic_sheet_music.harmony import build_analysis
    from agentic_sheet_music.harmony.cadence import find_cadences
    from agentic_sheet_music.harmony.chord_extraction import extract_chords
    from agentic_sheet_music.harmony.key_detection import detect_keys
    from agentic_sheet_music.harmony.roman import assign_roman
    from agentic_sheet_music.omr.ingest import ingest_all
    from agentic_sheet_music.omr.pdf_to_musicxml import which_audiveris
    from agentic_sheet_music.player.synth import render_audio
    from agentic_sheet_music.playalong.build import build_playalong

    if which_audiveris() is None or not MILONGA_PDF.exists() or not MILONGA_OMR.exists():
        pytest.skip("Audiveris / milonga fixtures not available")

    scores = ingest_all(MILONGA_PDF)
    analyses = []
    midi_paths = []
    for i, sc in enumerate(scores):
        chords = extract_chords(sc, max_chords_per_measure=1)
        regions = detect_keys(sc)
        rns, amb = assign_roman(chords, regions)
        cads = find_cadences(rns)
        analyses.append(
            build_analysis(
                score=sc,
                key_regions=regions,
                chords=chords,
                roman_numerals=rns,
                cadences=cads,
                ambiguities=amb,
            )
        )
        ar = render_audio(sc, tmp_path / f"audio-mvt{i + 1}")
        midi_paths.append(ar.midi)

    annotated = tmp_path / "annotated.pdf"
    annotate_pdf_all_movements(MILONGA_PDF, MILONGA_OMR, tuple(analyses), annotated)

    out_dir = tmp_path / "playalong"
    index = build_playalong(
        source_pdf=MILONGA_PDF,
        annotated_pdf=annotated,
        omr_book=MILONGA_OMR,
        analyses=tuple(analyses),
        midi_paths=tuple(midi_paths),
        output_dir=out_dir,
        vendor_cache=_stub_vendor,
    )

    assert index == out_dir / "index.html"
    assert index.exists()
    # Vendor assets copied in.
    assert (out_dir / "vendor" / "spessasynth_lib.js").exists()
    assert (out_dir / "vendor" / "piano.sf3").exists()
    # One page PNG per source page (6 for milonga).
    pngs = sorted((out_dir / "pages").glob("page-*.png"))
    assert len(pngs) == 6, f"expected 6 page PNGs, got {[p.name for p in pngs]}"
    # One measures.json per movement.
    for i in range(2):
        mpath = out_dir / f"measures-mvt{i + 1}.json"
        assert mpath.exists(), f"{mpath} missing"
        data = json.loads(mpath.read_text())
        assert isinstance(data, list)
        assert len(data) >= 20, f"mvt{i + 1} measures.json is sparse: {len(data)}"
        # Each entry has the expected fields.
        first = data[0]
        for key in ("measure", "page", "x0", "y0", "x1", "y1", "onset_seconds"):
            assert key in first, f"missing key {key} in {first}"
        # Strict monotonic onset within the movement.
        prev_t = -1.0
        for entry in data:
            assert entry["onset_seconds"] > prev_t
            prev_t = entry["onset_seconds"]
    # The MIDI used by the movement switcher should be accessible locally.
    assert (out_dir / "midi" / "mvt1.mid").exists()
    assert (out_dir / "midi" / "mvt2.mid").exists()
    # serve.sh exists for Safari users.
    assert (out_dir / "serve.sh").exists()
    # index.html references vendor + midi + measures paths (some are built
    # dynamically from the movement selector).
    html = index.read_text()
    for needle in (
        "vendor/spessasynth_lib.js",
        "vendor/piano.sf3",
        'const MIDI_DIR = "./midi"',
        "measures-mvt",  # built as `./measures-mvt${mvt}.json` in JS
    ):
        assert needle in html, f"index.html missing reference to {needle}"

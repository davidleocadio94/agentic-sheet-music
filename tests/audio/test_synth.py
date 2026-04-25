"""Tests for audio. Spec: specs/feature-audio.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_sheet_music.omr.ingest import ingest

FIX_CHORDS = (
    Path(__file__).parent.parent
    / "fixtures"
    / "harmony-chord-extraction"
    / "block-chords.musicxml"
)
FIX_EMPTY = (
    Path(__file__).parent.parent / "fixtures" / "harmony-key-detection" / "empty.musicxml"
)
MILONGA_PDF = Path("/Users/davidvillarreal/Documents/Music/ClassicalGuitar/milonga.pdf")


def test_renders_midi_for_block_chords(tmp_path: Path) -> None:
    from agentic_sheet_music.player.synth import render_audio

    score = ingest(FIX_CHORDS)
    result = render_audio(score, tmp_path)
    assert result.midi == tmp_path / "full.mid"
    assert result.midi.exists()
    data = result.midi.read_bytes()
    assert data.startswith(b"MThd"), "expected a valid MIDI header"
    assert len(data) > 50


def test_section_clips_written(tmp_path: Path) -> None:
    from agentic_sheet_music.player.synth import render_audio

    score = ingest(FIX_CHORDS)
    result = render_audio(score, tmp_path, sections=((1, 2), (3, 4)))
    assert (tmp_path / "sections" / "m1-2.mid").exists()
    assert (tmp_path / "sections" / "m3-4.mid").exists()
    section_ranges = {(s.start_measure, s.end_measure) for s in result.section_wavs}
    assert section_ranges == {(1, 2), (3, 4)}


def test_returns_none_wav_when_fluidsynth_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentic_sheet_music.player import synth

    monkeypatch.setattr(synth, "_which_fluidsynth", lambda: None)
    score = ingest(FIX_CHORDS)
    result = synth.render_audio(score, tmp_path)
    assert result.full_wav is None
    assert result.midi.exists()


def test_empty_score_raises(tmp_path: Path) -> None:
    from agentic_sheet_music.player.synth import AudioRenderError, render_audio

    score = ingest(FIX_EMPTY)
    with pytest.raises(AudioRenderError):
        render_audio(score, tmp_path)


def test_section_out_of_range_skipped(tmp_path: Path) -> None:
    from agentic_sheet_music.player.synth import render_audio

    score = ingest(FIX_CHORDS)  # 4 measures
    result = render_audio(score, tmp_path, sections=((1, 999),))
    assert result.section_wavs == ()
    assert not (tmp_path / "sections" / "m1-999.mid").exists()


@pytest.mark.omr_binary
def test_milonga_midi_uses_piano_not_voice(tmp_path: Path) -> None:
    """Correctness: default MIDI program must be Acoustic Grand Piano (GM 0),
    not whatever Audiveris guessed (it defaults to 'Voice Oohs' = GM 53).
    """
    from agentic_sheet_music.omr.pdf_to_musicxml import which_audiveris
    from agentic_sheet_music.player.synth import render_audio

    if which_audiveris() is None or not MILONGA_PDF.exists():
        pytest.skip("Audiveris / milonga fixtures not available")

    score = ingest(MILONGA_PDF)
    result = render_audio(score, tmp_path)

    # Parse the MIDI properly via music21 (a naive byte-scan for 0xC0..0xCF
    # catches false positives inside delta-time and velocity payloads).
    from music21 import midi as m21midi

    mf = m21midi.MidiFile()
    mf.open(str(result.midi))
    try:
        mf.read()
    finally:
        mf.close()
    programs: list[int] = []
    for track in mf.tracks:
        for ev in track.events:
            if ev.type == m21midi.ChannelVoiceMessages.PROGRAM_CHANGE:
                programs.append(int(ev.data))
    assert programs, "expected at least one MIDI Program Change event"
    assert all(p == 0 for p in programs), (
        f"MIDI should use program 0 (Acoustic Grand Piano), got programs {programs}. "
        f"Audiveris's default 'Voice Oohs' (program 53) is what caused the "
        f"'weird synth' sound."
    )


@pytest.mark.omr_binary
def test_milonga_midi_correctness(tmp_path: Path) -> None:
    """Correctness: the milonga MIDI must be a real, substantial, tempo-bearing file."""
    from agentic_sheet_music.harmony.cadence import find_cadences
    from agentic_sheet_music.harmony.chord_extraction import extract_chords
    from agentic_sheet_music.harmony.key_detection import detect_keys
    from agentic_sheet_music.harmony.roman import assign_roman
    from agentic_sheet_music.omr.pdf_to_musicxml import which_audiveris
    from agentic_sheet_music.player.synth import render_audio

    if which_audiveris() is None or not MILONGA_PDF.exists():
        pytest.skip("Audiveris / milonga fixtures not available")

    score = ingest(MILONGA_PDF)
    chords = extract_chords(score, max_chords_per_measure=1)
    regions = detect_keys(score)
    rns, _ = assign_roman(chords, regions)
    cads = find_cadences(rns)

    # Build per-cadence section clips for the first three cadences.
    sections = tuple(
        (c.start_measure, c.end_measure) for c in cads[:3]
    )
    result = render_audio(score, tmp_path, sections=sections)

    assert result.midi.stat().st_size > 2048, "milonga MIDI suspiciously small"
    # Every cadence we asked for must produce an extant MIDI clip.
    emitted = {(s.start_measure, s.end_measure) for s in result.section_wavs}
    assert set(sections).issubset(emitted), f"missing section clips: {set(sections) - emitted}"
    for s, e in sections:
        assert (tmp_path / "sections" / f"m{s}-{e}.mid").exists()

    # The MIDI must contain a SET_TEMPO event (0xFF 0x51). We check any byte
    # sequence that includes the meta-event signature.
    data = result.midi.read_bytes()
    assert b"\xff\x51" in data, "no tempo meta-event found in milonga MIDI"


@pytest.mark.audio_binary
def test_milonga_wav_integration(tmp_path: Path) -> None:
    """If fluidsynth + SoundFont available, WAV rendering produces a real RIFF file."""
    from agentic_sheet_music.omr.pdf_to_musicxml import which_audiveris
    from agentic_sheet_music.player import synth

    if which_audiveris() is None or not MILONGA_PDF.exists():
        pytest.skip("Audiveris / milonga fixtures not available")
    if synth._which_fluidsynth() is None or synth._which_soundfont(None) is None:
        pytest.skip("fluidsynth or SoundFont not installed")

    score = ingest(MILONGA_PDF)
    result = synth.render_audio(score, tmp_path)
    assert result.full_wav is not None and result.full_wav.exists()
    data = result.full_wav.read_bytes()
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"
    assert result.full_wav.stat().st_size > 10 * 1024

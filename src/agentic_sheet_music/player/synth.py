"""MIDI + optional WAV rendering. See specs/feature-audio.md.

MIDI is produced natively by music21 (`stream.write('midi', fp=...)`). WAV is
optional and requires both `fluidsynth` (on PATH or explicit binary) and a
SoundFont. We never block MIDI output on missing audio tooling.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from music21 import converter, instrument

from agentic_sheet_music.types import AudioRender, Score, SectionAudio

logger = logging.getLogger(__name__)

_COMMON_SOUNDFONTS: tuple[Path, ...] = (
    Path("/opt/homebrew/share/sounds/sf2/FluidR3_GM.sf2"),
    Path("/usr/local/share/sounds/sf2/FluidR3_GM.sf2"),
    Path("/usr/share/sounds/sf2/FluidR3_GM.sf2"),
    Path.home() / ".fluidsynth" / "FluidR3_GM.sf2",
)


class AudioRenderError(Exception):
    pass


def render_audio(
    score: Score,
    output_dir: Path,
    *,
    sections: tuple[tuple[int, int], ...] = (),
    soundfont_path: Path | None = None,
    fluidsynth_binary: Path | None = None,
) -> AudioRender:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    stream = converter.parse(str(score.musicxml_path))
    if not list(stream.flatten().notes):
        raise AudioRenderError(f"{score.musicxml_path}: no notes to render")

    _force_piano(stream)

    full_midi = output_dir / "full.mid"
    stream.write("midi", fp=str(full_midi))

    actual_measures = sorted(
        {int(m.number) for m in stream.recurse().getElementsByClass("Measure") if m.number}
    )
    last_measure = actual_measures[-1] if actual_measures else 0

    section_audio: list[SectionAudio] = []
    sections_dir = output_dir / "sections"
    for start, end in sections:
        if end > last_measure or start > last_measure or start > end:
            logger.warning(
                "section m%d-%d is outside the piece (last measure %d); skipping",
                start,
                end,
                last_measure,
            )
            continue
        clip = stream.measures(start, end)
        if clip is None or not list(clip.flatten().notes):
            logger.warning("section m%d-%d has no notes; skipping", start, end)
            continue
        sections_dir.mkdir(parents=True, exist_ok=True)
        clip_midi = sections_dir / f"m{start}-{end}.mid"
        clip.write("midi", fp=str(clip_midi))
        section_audio.append(
            SectionAudio(start_measure=start, end_measure=end, wav_path=clip_midi)
        )

    fs = fluidsynth_binary or _which_fluidsynth()
    sf = _which_soundfont(soundfont_path)
    full_wav: Path | None = None
    if fs is not None and sf is not None:
        full_wav_path = output_dir / "full.wav"
        if _synthesize(full_midi, full_wav_path, sf, fs):
            full_wav = full_wav_path
        refreshed: list[SectionAudio] = []
        for s in section_audio:
            wav_path = s.wav_path.with_suffix(".wav")
            if _synthesize(s.wav_path, wav_path, sf, fs):
                refreshed.append(
                    SectionAudio(
                        start_measure=s.start_measure,
                        end_measure=s.end_measure,
                        wav_path=wav_path,
                    )
                )
            else:
                refreshed.append(s)
        section_audio = refreshed
    else:
        missing = []
        if fs is None:
            missing.append("fluidsynth (brew install fluidsynth)")
        if sf is None:
            missing.append(
                "SoundFont (set SHEETMUSIC_SOUNDFONT or install FluidR3_GM.sf2)"
            )
        logger.info("WAV rendering skipped; missing: %s", ", ".join(missing))

    return AudioRender(
        score=score,
        full_wav=full_wav,
        midi=full_midi,
        section_wavs=tuple(section_audio),
    )


def _force_piano(stream: object) -> None:
    """Replace every part's instrument with Acoustic Grand Piano (GM program 0).

    Audiveris OMR output declares the part as "Voice Oohs" (GM 53) by default
    because MusicXML doesn't carry instrument hints, and music21 uses whatever
    is in the `<score-instrument>`. A guitar/piano score should play on piano,
    not synth vocals. The user can override by passing a pre-prepared MusicXML
    with the instrument set explicitly.
    """
    piano = instrument.Piano()  # GM program 0
    for part in stream.parts:  # type: ignore[attr-defined]
        # Remove any existing instruments, then insert piano at the start.
        for existing in list(part.getElementsByClass("Instrument")):
            part.remove(existing, recurse=True)
        part.insert(0, piano)


def _which_fluidsynth() -> Path | None:
    found = shutil.which("fluidsynth")
    return Path(found) if found else None


def _which_soundfont(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.exists() else None
    env = os.environ.get("SHEETMUSIC_SOUNDFONT")
    if env:
        p = Path(env)
        if p.exists():
            return p
    for candidate in _COMMON_SOUNDFONTS:
        if candidate.exists():
            return candidate
    return None


def _synthesize(
    midi_path: Path, wav_path: Path, soundfont: Path, binary: Path
) -> bool:
    cmd = [
        str(binary),
        "-ni",
        "-F",
        str(wav_path),
        "-r",
        "44100",
        "-g",
        "0.8",
        str(soundfont),
        str(midi_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("fluidsynth failed on %s: %s", midi_path, e)
        return False
    if result.returncode != 0:
        logger.warning(
            "fluidsynth returned %d on %s:\n%s",
            result.returncode,
            midi_path,
            result.stderr[-500:],
        )
        return False
    return True

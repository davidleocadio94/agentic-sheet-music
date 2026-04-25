# Feature spec — audio

## Problem

Roman numerals describe what the piece does; the user also wants to *hear* it. MIDI files are the universally supported deliverable — playable in every DAW and by any browser with a light JS player. For local playback in a PDF viewer that can't play MIDI, WAV renders are the fallback; those need `fluidsynth` + a SoundFont.

## Approach

`music21.stream.write('midi', ...)` produces MIDI natively — always available. WAV rendering shells out to `fluidsynth` when both the binary and a SoundFont file are findable; otherwise `full_wav` is `None` and the user is told how to install.

Per-section clips are produced by re-sliced MIDI (then re-synthesized for WAV) rather than audio splicing — the MIDI is the source of truth.

## Inputs

- `score: Score` — already ingested (single movement).
- `output_dir: Path` — where artifacts go. Created if missing; existing files are overwritten because MIDI is deterministic from the score.
- `sections: tuple[tuple[int, int], ...] = ()` — (start_measure, end_measure) pairs to clip.
- `soundfont_path: Path | None = None` — override. Otherwise check env `SHEETMUSIC_SOUNDFONT`, then common locations.
- `fluidsynth_binary: Path | None = None` — override. Otherwise PATH lookup.

## Outputs

`AudioRender` (already declared in `types.py`):
- `score`
- `midi: Path` — always set
- `full_wav: Path | None` — None if fluidsynth/SoundFont missing
- `section_wavs: tuple[SectionAudio, ...]`

Output layout:
```
<output_dir>/
  full.mid
  full.wav                 # when fluidsynth + SoundFont are available
  sections/
    m1-4.mid
    m1-4.wav               # when fluidsynth available
    ...
```

For multi-movement scores the caller renders each movement to its own subdirectory (`<output_dir>/mvt1/`, `mvt2/`, etc.). Keeping the core function single-movement avoids tangling path conventions here.

## Algorithm

1. Parse `score.musicxml_path` via `music21.converter.parse`.
2. Raise `AudioRenderError` if no notes.
3. Write `output_dir/full.mid` via `stream.write('midi', fp=...)`.
4. For each `(start, end)` in `sections`:
   - `stream.measures(start, end)` — skip the clip if empty or out-of-range.
   - Write `output_dir/sections/m{start}-{end}.mid`.
5. Locate fluidsynth + SoundFont. If both available, shell out for every `.mid`:
   `fluidsynth -ni -F <out.wav> -r 44100 -g 0.8 <sf2> <mid>`.
6. Return an `AudioRender`.

## Edge cases

- **No notes** → `AudioRenderError("no notes to render")`. (Matches the existing stage conventions.)
- **`output_dir` equals the directory of the source PDF** → refuse. Derivatives live separately.
- **Section range out of piece** → `stream.measures` returns an empty/invalid slice; skip with a warning.
- **fluidsynth missing** → `full_wav=None`, log a one-line "install fluidsynth for WAV playback," keep MIDI.
- **SoundFont missing** → same as above. If `soundfont_path` was explicit, log the missing path; otherwise, print the env var + common-path hints.
- **WAV render returns non-zero** → `full_wav=None`, log; never crash.

## Test cases

Fixtures: reuse `block-chords.musicxml` (4 measures, valid notes) and `empty.musicxml`.

Tests in `tests/audio/test_synth.py`:

- `test_renders_midi_for_block_chords` — `full.mid` exists, starts with `MThd`, > 50 bytes.
- `test_section_clips_written` — sections `((1,2), (3,4))` → two MIDI files, `section_wavs` lists them even if WAV is None.
- `test_returns_none_wav_when_fluidsynth_missing` — monkey-patch the locator; MIDI still written, `full_wav is None`.
- `test_empty_score_raises` — `AudioRenderError`.
- `test_section_out_of_range_skipped` — `((1, 999),)` → no crash, no clip file emitted.
- **Correctness** (`@pytest.mark.omr_binary`): render the milonga to MIDI, assert:
  - `full.mid` > 2 KB (57-measure piece, real content).
  - At least one section clip was created for each of the first 3 cadences in the analysis.
  - The MIDI tempo header is present (asserts music21 actually wrote a tempo event).
- **WAV integration** (`@pytest.mark.audio_binary`): if fluidsynth + SoundFont found, `full.wav` > 10 KB and is RIFF/WAVE format (bytes `RIFF` at offset 0, `WAVE` at offset 8). Self-skip otherwise.

## Non-goals

- Tempo overrides, dynamic shaping, instrument remapping. Take the MusicXML as truth.
- Per-voice stems / multi-track WAV.
- Realistic guitar articulation. Default `FluidR3_GM` is fine for v1.
- Normalization / EQ / compression.
- Browser playback UI. Paths are returned; consumers embed `<audio>` etc.
- Installing fluidsynth for the user. Detect and tell; don't silently `brew install`.

## Design sketch

```python
# src/agentic_sheet_music/player/synth.py

class AudioRenderError(Exception): ...

def render_audio(
    score: Score,
    output_dir: Path,
    *,
    sections: tuple[tuple[int, int], ...] = (),
    soundfont_path: Path | None = None,
    fluidsynth_binary: Path | None = None,
) -> AudioRender: ...
```

Helpers:
- `_which_fluidsynth() -> Path | None`
- `_which_soundfont(explicit: Path | None) -> Path | None` — check env, then a short list of brew/macOS common paths.
- `_synthesize(midi_path, wav_path, soundfont, binary) -> bool`

## Correctness guardrail

Per `.claude/rules/correctness.md`, "produces output" isn't enough. The milonga MIDI should be *listenable*; the integration test asserts size + tempo presence + at least one cadence-bound section clip.

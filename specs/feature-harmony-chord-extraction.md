# Feature spec — harmony-chord-extraction

## Problem

Roman-numeral analysis needs **chord events** as its input: "at measure 3 beat 1, the sounding harmony is D minor." A raw MusicXML score has *notes*, not chords — and for arpeggiated textures like a guitar milonga, the chord tones are spread across multiple beats rather than struck simultaneously. A naive `chordify()` on such a piece produces one single-note "chord" per onset, which is useless for harmonic analysis.

We need a function that collapses arpeggiated and broken-chord textures into real harmonic events at the piece's actual harmonic rhythm.

## Inputs

- `score: Score` from `agentic_sheet_music.types` (ingested MusicXML).
- `max_chords_per_measure: int = 2` — upper bound on harmonic rhythm. Most tonal repertoire sits at 1–2 chords per measure; use 4 for dense figured-bass style. Exposed as a knob; downstream stages may re-infer it per region.

## Outputs

- `tuple[ChordEvent, ...]` (from `agentic_sheet_music.types`), one per reduced chord, in score order.
- Each `ChordEvent` carries:
  - `measure` (int) — MusicXML measure number.
  - `beat` (float) — beat position within the measure (1-indexed, fractional). `1.0` = downbeat.
  - `pitches` — the set of pitch classes that make up the reduced chord, as strings like `"D4"`, `"F4"`, `"A4"`. Duplicates removed; octave preserved for the lowest occurrence of each pitch class (so bass position can be inferred later).
  - `label` — a chord-quality guess like `"Dm"`, `"G7"`, `"F"`, or the literal `"?"` if the reducer produced something that isn't a recognizable triad/seventh. Roman-numeral assignment happens in a later stage — `label` is a rough surface-level tag for debugging and display.

## Algorithm

**Design pivot during implementation:** the spec originally called for `music21.analysis.reduceChords.ChordReducer`. Two problems surfaced:

1. `ChordReducer.fillMeasureGaps` hits `TypeError: unhashable type: 'PitchedTimespan'` on any score with adjacent timespans sharing pitches — i.e. arpeggiated figuration, which is the whole point of this module. Bug reproducible in music21 9.9.1; patched locally via `src/agentic_sheet_music/harmony/_music21_patches.py` (make `PitchedTimespan` identity-hashable). Remove when upstream fixes.
2. Even with the patch applied, `ChordReducer.collapseArpeggios` only merges **adjacent runs of identical pitches** — it does NOT reduce a broken-chord F-A-C-A to an F-major harmony. Verified empirically: a pure arpeggio passes through as N single-note "chords", which is useless.

Replaced with a deterministic chordify + bucket pass:

1. `stream.chordify()` — one vertical sonority per unique onset.
2. Per measure, divide the measure duration into `max_chords_per_measure` windows.
3. For each window, union the pitch classes that *sound during* that window. Keep the lowest-octave occurrence of each pitch class for bass inference.
4. Build a `music21.chord.Chord` from the unique pitches, label via `pitchedCommonName`.

This is the approach DCMLab's `ms3` pipeline uses. It's simpler, more predictable, and doesn't depend on music21 internals.

Downside: with no non-chord-tone filtering, a window that contains chord tones + passing tones produces a 4-or-more-note stack that doesn't match any standard quality, so the `label` falls back to `"?"`. Real-world label recognition rate on the Cardoso milonga is ~23% — acceptable for this spec because roman-numeral labeling is the downstream stage's job, and NCT filtering is a separate spec.

## Edge cases

- **Empty measure (all rests)** → skip; emit no chord for that measure.
- **Single sustained note across a measure** → one `ChordEvent` with one pitch and label `"?"` (not a chord). Flag for downstream but don't drop.
- **Two-voice writing on one staff** — `ChordReducer` respects voices within a part; no extra handling needed.
- **Grace notes and trills** — `ChordReducer` weights by duration, so these get near-zero weight and don't distort the reduction. No explicit filtering.
- **Score with no notes at all** → raise `ChordExtractionError`. (Matches `key_detection.KeyDetectionError` pattern.)
- **`max_chords_per_measure <= 0`** → `ValueError`.
- **Audiveris output with its "Voice Oohs" default instrument** — irrelevant; we operate on notes, not instrument metadata.

## Test cases

Fixtures in `tests/fixtures/harmony-chord-extraction/`:

1. `block-chords.musicxml` — 4 measures of block triads in F major: `F | Bb | C | F`. Each chord is struck as a simultaneity. Asserts 4 chord events, one per measure, labels `F`, `Bb`, `C`, `F`.
2. `arpeggiated.musicxml` — same 4-chord progression but arpeggiated (each chord broken into four eighth notes across a 4/4 measure). Must still reduce to the same 4 events. **This is the critical test — if `ChordReducer` fails here, the whole harmony stack fails.**
3. `two-chords-per-measure.musicxml` — 2 measures of 2/4 where each measure has a half-measure harmonic rhythm: e.g. `Dm Gm | A Dm`. Asserts 4 events (beats 1 and 2 in each measure).
4. `empty-measure.musicxml` — 3-measure piece where the middle measure is rests. Asserts 2 events, from the outer measures only.

Tests in `tests/harmony/test_chord_extraction.py`:

- `test_block_chords_extract_cleanly` — fixture 1 → 4 events with the expected labels in order.
- `test_arpeggiated_reduces_to_block_progression` — fixture 2 → 4 events with the same pitch-class content as fixture 1. Labels may differ slightly (inversion labeling) but pitch-class sets must match.
- `test_two_chords_per_measure` — fixture 3 with `max_chords_per_measure=2` → 4 events; with default (2) or higher.
- `test_skips_empty_measures` — fixture 4 → exactly 2 events, neither in the empty measure.
- `test_raises_on_empty_score` — use the `empty.musicxml` fixture from harmony-key-detection → `ChordExtractionError`.
- `test_invalid_max_chords_raises` — `max_chords_per_measure=0` → `ValueError`.
- **Real-world smoke** (marked `@pytest.mark.omr_binary`): run on the milonga MusicXML from the earlier OMR run, assert ≥20 chord events extracted from the 57-measure score (2/4 meter → expect ~1 chord/measure → should see 50+ events; the lower bound accepts partial extraction).

Happy-path fixture for the initial RED test: `block-chords.musicxml`.

## Non-goals

- Roman-numeral labeling — separate spec (`harmony-roman`).
- Non-chord tone removal — `ChordReducer` already filters short-duration pitches via its weight algorithm; finer-grained NCT classification is its own spec.
- Cadence detection — downstream.
- Figured-bass / inversion symbols beyond the rough `label` — downstream.
- Chord-quality labeling beyond major/minor/7 — acceptable to fall back to `"?"`. Quality inference is hard and belongs in the roman-numeral stage where key context disambiguates.
- Harmonic rhythm *detection* (auto-picking `max_chords_per_measure`) — the caller passes a constant for v1. Automatic detection is a follow-up when we have enough repertoire to tune against.

## Design sketch

```python
# src/agentic_sheet_music/harmony/chord_extraction.py

class ChordExtractionError(Exception): ...

def extract_chords(
    score: Score,
    max_chords_per_measure: int = 2,
) -> tuple[ChordEvent, ...]:
    ...
```

Internally:
- `_parse(score) -> music21.stream.Score` — cached re-parse; same as key_detection does (we'll dedupe later via a score-cache module if it matters).
- `_reduce_measure(measure, n) -> list[music21.chord.Chord]` — wrap `ChordReducer.reduceMeasureToNChords`.
- `_to_event(chord, measure_number, beat) -> ChordEvent` — extract pitches + guess label.

## Interaction with downstream

- `harmony-roman` will consume `(tuple[ChordEvent, ...], tuple[KeyRegion, ...])` and emit `tuple[RomanEvent, ...]`. It handles inversion and secondary-dominant labeling; this stage only needs to produce pitch-set-correct events.
- `music-theory-reviewer` agent reads the downstream `HarmonyAnalysis` and may flag a region where the chord reduction looks wrong (e.g. "this phrase clearly has 4 chords per measure but only 2 were extracted"). In that case the caller re-runs `extract_chords` with a higher `max_chords_per_measure` for the disputed region — the function is cheap and side-effect free.

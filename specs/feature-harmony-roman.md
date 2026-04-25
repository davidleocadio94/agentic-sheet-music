# Feature spec — harmony-roman

## Problem

`extract_chords` gives us `ChordEvent`s with pitch content + rough labels (`"Dm"`, `"Bbmaj7"`, or `"?"`). `detect_keys` gives us `KeyRegion`s assigning a key to every measure. We need to bring those together into **roman-numeral events**: "measure 9 beat 1, D minor region → i" or "measure 24 beat 1, G minor region → V7/iv." That's the first output that's musically meaningful — a student reading the deck understands "i-iv-V-i," not "Dm-Gm-A-Dm."

This is also where we address the biggest gap from chord-extraction: the high `?` label rate on real repertoire is caused by **non-chord tones** (passing tones, neighbor tones) inflating each window's pitch set. This spec includes NCT-aware reduction *during* roman-numeral assignment — because key context is what makes NCT classification tractable.

## Inputs

- `chords: tuple[ChordEvent, ...]` from `extract_chords`
- `key_regions: tuple[KeyRegion, ...]` from `detect_keys`
- `score: Score` — only for the MusicXML path (used to re-fetch beat-strength info during NCT filtering)

## Outputs

- `tuple[RomanEvent, ...]` — one per input `ChordEvent`, same order, same `(measure, beat)`.
- Each `RomanEvent` carries:
  - `measure`, `beat` — copied from the source `ChordEvent`
  - `numeral` — roman numeral string (`"i"`, `"V7"`, `"V7/iv"`, `"bVI"`, `"It+6"`, or `"?"` as last resort)
  - `key` — the key string from the containing `KeyRegion` (e.g. `"D minor"`)
  - `rationale` — one-sentence justification (for debugging, agent review, and slide generation). E.g., `"dominant of iv (Gm): V7/iv resolves to iv in m.25"`.

Also returns `tuple[Ambiguity, ...]` for chords the labeler declined to commit to — two or more equally defensible readings.

## Algorithm

For each `ChordEvent`:

1. **Find the key region** whose measure range contains `event.measure`. Call this `key_region.key` (parsed into a `music21.key.Key`).
2. **NCT-aware chord tone selection.** The `event.pitches` may include non-chord tones. Filter:
   - Consult the key's diatonic triad roots (tonic, supertonic, ..., leading-tone). For each diatonic triad (plus its common secondary dominants), count how many of `event.pitches` are chord tones for it.
   - The candidate with the most chord-tone matches and fewest "extra" pitches wins. Ties broken by (a) preferring the chord whose root lies in the lowest octave of the event, (b) preferring diatonic over chromatic.
   - If no diatonic/secondary-dominant candidate matches at least 2 pitches, fall back to labeling the pitch set as-is via `music21.roman.romanNumeralFromChord`.
3. **Label via `music21.roman.RomanNumeral(figure, key)`** (never mutate `.key` — known music21 bug #1344). Figure is the figure we inferred in step 2.
4. **Secondary-dominant check (look-ahead).** If the inferred chord is a dominant-7 or diminished of a non-tonic degree:
   - Compare its root to the next `ChordEvent`'s most likely root. If the next chord's root is the expected resolution (V7/X → X, viio/X → X), relabel as `V7/X` or `viio/X`.
   - If it doesn't resolve as expected but is spelled like a secondary dominant, record as an ambiguity rather than forcing a label.
5. **Ambiguity emission.** Any event where two candidates tied at step 2, or where a secondary-dominant check produced a plausible alternative, gets added to the ambiguities tuple.

## Edge cases

- **Empty chord event** (shouldn't happen, but defensive) → skip.
- **Event falls outside all key regions** (shouldn't happen if `detect_keys` is correct) → use the nearest region, flag as ambiguity.
- **Key region with `?` key** (shouldn't happen) → fallback label is literal `"?"`, rationale `"no key region available"`.
- **Pitch set with fewer than 2 chord tones for any diatonic triad** (very chromatic passage) → fallback to `romanNumeralFromChord` without NCT filtering; rationale notes `"chromatic; no clean chord-tone match"`.
- **Chord spelled enharmonically wrong** by OMR (e.g., a D♭ where score actually has C#) → roman-numeral logic works on pitch class, mostly OK; note the spelling mismatch in rationale only when it affects the label.
- **No key regions provided** → `ValueError("harmony-roman requires at least one key region")`.

## Test cases

Fixtures in `tests/fixtures/harmony-roman/`:

1. `c-major-I-IV-V-I.musicxml` — 4 block chords, one per measure, in C major. Expected: `I - IV - V - I`.
2. `d-minor-i-iv-V7-i.musicxml` — 4 chords in D minor: `Dm - Gm - A7 - Dm`. Expected: `i - iv - V7 - i`. Tests minor mode + dominant 7.
3. `c-major-with-passing.musicxml` — 2 measures, 4/4, each measure has I chord with a passing-tone in the melody: `C E G | (C D E G) (C E F G) (C E G C)`. Expected: both events labeled `I` despite the D/F passing tones.
4. `secondary-dominant.musicxml` — 4 measures in C major: `C - A7 - Dm - G7`. Expected: `I - V7/ii - ii - V7` (the A7 resolves to Dm, confirming secondary dominant).
5. `ambiguous-chromatic.musicxml` — 2 measures of chromatic motion with no clear harmony. Expected: at least one `Ambiguity` recorded.

Tests in `tests/harmony/test_roman.py`:

- `test_c_major_IV_V_I` — fixture 1 + key region `C major (1-4)` → numerals `["I", "IV", "V", "I"]`.
- `test_d_minor_with_V7` — fixture 2 + key region `D minor (1-4)` → numerals `["i", "iv", "V7", "i"]`.
- `test_ignores_passing_tones` — fixture 3: both events label as `I` even with NCT in the pitch set.
- `test_secondary_dominant_resolves` — fixture 4: m.2 labeled `V7/ii`, rationale mentions resolution to ii.
- `test_ambiguity_recorded_for_chromatic` — fixture 5: at least one ambiguity emitted.
- `test_requires_key_regions` — empty `key_regions` → `ValueError`.
- **Real-world smoke** (`@pytest.mark.omr_binary`): full milonga pipeline (`ingest → extract_chords → detect_keys → assign_roman`) produces at least 40 roman-numeral events, the bulk labeled with recognizable numerals (not `?`) — threshold: ≥50% non-`?` because this is where NCT filtering earns its keep.

## Non-goals

- Cadence detection — separate spec (`harmony-cadence`) operates on `tuple[RomanEvent, ...]`.
- Augmented sixth chords, Neapolitan sixth, CTo7, common-tone chords — add as fixtures + rules incrementally. Initial implementation emits `"?"` or a best-effort numeral with a rationale that admits the gap.
- Figured bass / inversion symbols beyond what `music21.roman.RomanNumeral.figure` already produces. We don't try to improve on music21's inversion handling.
- AugmentedNet cross-check — deferred to v1 per earlier research.
- Non-Western harmony. Stay on common-practice tonal.

## Design sketch

```python
# src/agentic_sheet_music/harmony/roman.py

class RomanAnalysisError(Exception): ...

def assign_roman(
    chords: tuple[ChordEvent, ...],
    key_regions: tuple[KeyRegion, ...],
    *,
    score: Score | None = None,  # reserved for NCT weight-aware filtering
) -> tuple[tuple[RomanEvent, ...], tuple[Ambiguity, ...]]:
    ...
```

Key internal helpers:
- `_key_for_measure(regions, measure) -> music21.key.Key`
- `_candidate_labels(pitches, key) -> list[(figure, confidence, rationale)]` — the NCT-filtered step 2
- `_check_secondary_dominant(current, next_chord, key) -> figure | None`
- `_to_music21_key(key_str) -> music21.key.Key` — map our "D minor" / "F major" strings

## Interaction with downstream stages

- `harmony-cadence` reads `tuple[RomanEvent, ...]` and pattern-matches `V→I` / `V→vi` / `IV→I` / `V→` etc. Our output must therefore be stable and ordered by `(measure, beat)`.
- `slide-designer` reads roman numerals + rationales when building per-phrase slides. Rationales should be educational, not debug-y.
- `music-theory-reviewer` agent reviews the tuple; its job gets easier when rationales are honest about uncertainty.

## Risk

The biggest failure mode is **over-confident labels on OMR-noisy scores**. Every RomanEvent with rationale hedging ("likely", "on-paper V but no resolution") should later translate to an ambiguity during the review pass. Don't let polish-sounding rationales hide uncertainty.

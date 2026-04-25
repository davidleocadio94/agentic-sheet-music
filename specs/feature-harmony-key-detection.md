# Feature spec — harmony-key-detection

## Problem

Every downstream harmonic label (roman numerals, cadences, tonicizations) is relative to a key. Before we can label a single chord, we need to know the local tonal center, and we need to detect when it changes. The MusicXML key *signature* is not enough — it tells us the accidentals, not the mode (e.g. `fifths=0` could be C major or A minor), and it can't describe a piece that modulates without a key change in the score.

## Inputs

- `score: Score` from `agentic_sheet_music.types` (an already-ingested MusicXML score).
- Optional `window_measures: int = 4` — window length for modulation detection (larger = more stable, smaller = detects shorter regions).

## Outputs

- `tuple[KeyRegion, ...]` covering the piece with **no gaps and no overlaps**. Every measure belongs to exactly one region.
- Each `KeyRegion` carries a non-`None` `confidence` (0.0–1.0) from the Krumhansl-Schmuckler correlation coefficient.

## Algorithm

Two-signal design: KS correlation is constrained by the MusicXML key signature. The signature is what the engraver wrote; ignoring it lets KS hallucinate distant keys on short arpeggiated windows (we hit this on the Cardoso milonga — KS alone labeled the D-minor opening as B♭ major because B♭'s profile matched the arpeggio better than D minor's).

1. **Read the MusicXML `<key><fifths>`** per measure. A signature of N fifths narrows candidates to the two parallel keys that share it (e.g. 1 flat → {F major, D minor}).
2. **Global key:** KS constrained to the initial signature's two candidates, on the whole-piece pitch histogram. This gives our home key.
3. **Windowed sweep:** for each `window_measures`-length window, compute both (a) the best KS-constrained-to-that-measure's-signature key and (b) the best unconstrained KS key across all 24 keys.
4. **Modulation acceptance:** an outside-signature key is only accepted when it beats the in-signature best by ≥ `KEY_SWITCH_MARGIN` (0.25) AND the same outside key holds that margin across ≥ `KEY_SWITCH_MIN_WINDOWS` (3) consecutive windows. Real modulations are sustained; KS hallucinations are short. These thresholds were tuned against the milonga so its opening arpeggio can't dislodge the declared home key.
5. **Region construction:** each window casts a weighted vote (its correlation) for every measure it covers; each measure is assigned its highest-voted key. A region must be ≥ `window_measures // 2` long — shorter "modulations" are treated as tonicizations and merged into the neighboring region with higher confidence (they're caught later at the roman-numeral stage). The `// 2` threshold (rather than full `window_measures`) lets us detect real modulations in short pieces where the total length is only 2–3× the window.
6. **Explicit mode:** when MusicXML has `<mode>major</mode>` or `<mode>minor</mode>`, that's used as a +0.05 tie-breaker between the signature's two candidates. We deliberately do NOT fall through to music21's `KeySignature.asKey().mode` default, which returns `major` when the MusicXML didn't declare mode — using that default gave us a false prior that biased F major over D minor on the milonga.
7. **Confidence** per region = mean correlation across its constituent windows.

## Edge cases

- **Single-measure piece** → one `KeyRegion` spanning just that measure, global KS only (skip sweep).
- **Score shorter than `window_measures`** → one region, global only.
- **All pitches identical / no tonal content** (pathological) → return one region with the global KS best guess and a low confidence (<0.3). Don't raise — downstream will see low confidence and flag it.
- **Empty score (no parts or no notes)** → raise `KeyDetectionError` with a clear message. Zero tonal content is distinct from ambiguous content.
- **Enharmonic ties** (G♯ minor vs A♭ minor): prefer the spelling consistent with the MusicXML key signature if it's within KS's top 3 results.

## Test cases

Fixtures in `tests/fixtures/harmony-key-detection/`:

1. `c-major-scale.musicxml` — reuse from omr-ingest. Obvious C major. One region, confidence > 0.9.
2. `a-minor-phrase.musicxml` — 4 measures of unambiguous A minor (A-B-C-D-E with G♯ leading tone). One region = A minor.
3. `c-to-g-modulation.musicxml` — 8 measures: first 4 clearly C major, last 4 clearly G major (introduces F♯ consistently). Two regions.
4. `ambiguous.musicxml` — a 4-measure chromatic fragment with no clear center. One region, confidence < 0.3.
5. `empty.musicxml` — valid MusicXML, no notes. Raises `KeyDetectionError`.

Tests in `tests/harmony/test_key_detection.py`:

- `test_detects_c_major_single_region` — fixture 1 → 1 region, key "C major", conf > 0.9.
- `test_detects_a_minor` — fixture 2 → 1 region, key "a minor".
- `test_detects_modulation_c_to_g` — fixture 3 → 2 regions; first C major covering m.1–4, second G major covering m.5–8. No gaps, no overlaps.
- `test_low_confidence_on_ambiguous` — fixture 4 → 1 region, confidence < 0.3.
- `test_raises_on_empty_score` — fixture 5 → `KeyDetectionError`.
- `test_regions_cover_all_measures_without_gaps` — property test over fixtures 1–3: union of region ranges == full measure range, no overlap.

Happy-path fixture for the initial RED test: `c-major-scale.musicxml`.

## Non-goals

- Functional analysis / roman numerals — that consumes `KeyRegion`s, separate spec.
- Tonicization detection (short secondary-key excursions) — belongs with roman-numeral analysis.
- Non-Western tonalities / modes beyond major and minor — out of scope for v1.
- Atonal / 12-tone — out of scope; the `ambiguous` case is the signal for atonal-ish content.

## Design sketch

```python
# src/agentic_sheet_music/harmony/key_detection.py

class KeyDetectionError(Exception): ...

def detect_keys(score: Score, window_measures: int = 4) -> tuple[KeyRegion, ...]:
    ...
```

Relies on `music21.analysis.discrete.KrumhanslSchmuckler` — it already implements the profile correlation; we wrap it in windowing + region merging.

## Interaction with the `harmony-analyst` agent

When this lands, `harmony-analyst` becomes the agent that *drives* this function for non-trivial scores: it calls `detect_keys`, inspects the regions, and if something looks musically suspicious (e.g., a 4-measure "B♭ major" island inside a C major prelude that's really just mixture), it notes the issue and proposes a fix. The pure function returns mechanical output; the agent adds musical judgment.

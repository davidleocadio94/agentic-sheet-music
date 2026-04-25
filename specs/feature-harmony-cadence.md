# Feature spec — harmony-cadence

## Problem

Roman numerals per event aren't yet a musical narrative. A phrase's *shape* — where it breathes and where it closes — is marked by **cadences**: authentic, plagal, half, deceptive, phrygian. A student reading the deck doesn't need to know every chord; they need to see *here is the phrase ending, and here's why*.

This stage consumes `tuple[RomanEvent, ...]` and returns a `tuple[Cadence, ...]` marking where the phrase-closure patterns happen.

## Inputs

- `rn_events: tuple[RomanEvent, ...]` from `assign_roman`, already ordered by `(measure, beat)`.
- Optional `min_phrase_length: int = 2` — don't label a cadence in the first two events of a piece (no phrase context yet).

## Outputs

- `tuple[Cadence, ...]` from `agentic_sheet_music.types`. Each `Cadence` carries:
  - `kind` — `"PAC"` | `"IAC"` | `"HC"` | `"DC"` | `"PC"` | `"PhC"`
  - `start_measure`, `end_measure` — the measure range the cadence pattern spans (usually 2 measures: the penultimate chord's measure and the final chord's)
  - `rationale` — one sentence: which RNs, in which key, and what makes this that cadence flavor

Cadences do not need to cover the whole piece; they're sparse markers.

## Algorithm

Pattern-match a sliding window of consecutive `RomanEvent`s, **per key region** (never across a key change). Patterns:

- **PAC** (Perfect Authentic) — `V` or `V7` → `I` (major) or `i` (minor), both on strong beats, no inversion indicators in the figure, and the same key in both events. This is the canonical full close.
  - Our data lacks soprano-line info (chordify flattens voicing), so we cannot distinguish PAC from IAC by the soprano ^1 rule. We label conservatively: any V→I with both chords in **root position** (figure exactly `V`/`V7`/`I`/`i`, not `V6`, `V65`, etc.) gets `PAC`; otherwise `IAC`.
- **IAC** (Imperfect Authentic) — `V`/`V7` → `I`/`i` with either chord inverted (figure contains digits other than `7`), or not on a strong beat. Same key both sides.
- **HC** (Half Cadence) — phrase ending on `V` (not resolving) when the next event is a rest, a clear new phrase start (tonic resumption), or end of the analysis. Heuristic: the next event is at least one measure later, or is a strong-beat tonic after at least a quarter-note gap.
- **DC** (Deceptive) — `V`/`V7` → `vi` (major) or `VI` (minor). Same key both sides.
- **PC** (Plagal) — `IV`/`iv` → `I`/`i`. Same key both sides. Lower-priority: only labeled when no authentic cadence is present within ±1 measure.
- **PhC** (Phrygian Half, minor keys only) — `iv6` → `V`. Our inversions are unreliable, so: a minor-key V preceded by iv in its first inversion, OR simply any minor-key iv→V where the bass pitch is scale-degree 6 (flat 6). For v1: match minor-key `iv`→`V`/`V7` with the prior RN's key being minor — label as PhC if no PAC immediately follows.

## Edge cases

- **RN list empty** → returns `()`, never raises.
- **Single event** → no cadence possible, returns `()`.
- **Cross-key V→I** (V at the end of an A-minor region, I at the start of a C-major region) → do NOT emit a cadence. Cadences are within-key events.
- **Chain of V7→I in rapid succession** (sequential cadential phrases) — emit one cadence per occurrence; downstream stages can cluster if desired.
- **`?` numeral** breaks any pattern through it — patterns only match on *explicit* RNs.
- **Ambiguity on either end** — the current `Ambiguity` list isn't consulted here (it's a separate signal). A noisy RN is treated at face value; downstream consumers cross-reference.
- **Last-event HC** — a piece ending on V gets one HC at the very end.

## Test cases

Fixtures in `tests/fixtures/harmony-cadence/`:

These fixtures are **synthetic** — we construct `RomanEvent` tuples directly in the test, since cadence detection operates on already-analyzed RNs, not MusicXML. Fixtures are `.py` builders in the test module itself. No MusicXML files needed.

Tests in `tests/harmony/test_cadence.py`:

- `test_pac_on_root_position_V_to_I` — `I-IV-V-I` in C major, root-position → one cadence, kind `PAC`, spanning the V-I measures.
- `test_iac_when_inverted_V` — `I-V6-I` → kind `IAC`.
- `test_hc_on_V_at_end` — `I-IV-V` (ending on V) → kind `HC`.
- `test_deceptive_cadence` — `I-IV-V7-vi` in C major → kind `DC`.
- `test_plagal_when_no_authentic` — `I-IV-I` → kind `PC`.
- `test_phrygian_half_in_minor` — `i-iv-V` in D minor → kind `PhC`.
- `test_does_not_cross_key_regions` — `V` in m.4 of A minor region, `I` in m.5 of C major region → no cadence emitted.
- `test_empty_rn_list_returns_empty` — `()` → `()`.
- `test_question_mark_breaks_pattern` — `V` → `?` → `I` → no cadence.
- **Real-world smoke** (`@pytest.mark.omr_binary`): run on the milonga, assert at least 3 cadences found (a 57-measure piece should have several phrase closures); spot-check that at least one is `PAC` or `IAC` on a `V7 → i` in D minor.

## Non-goals

- Half-cadence detection based on surface features other than "next event is far away." Real HC detection benefits from metric/phrasing analysis (strong-beat resolution, fermatas) — out of scope.
- Soprano-line PAC discrimination. Requires keeping voice information through chord extraction, which we deliberately flatten.
- Cadence clustering / phrase grouping. A follow-up spec uses the raw cadence list to build phrase structure.
- Evaded / expanded cadences. Defer.
- Modulation-setup cadences (a PAC in a new key is a modulation to that key). Interpretation belongs to the reviewer agent, not the detector.

## Design sketch

```python
# src/agentic_sheet_music/harmony/cadence.py

def find_cadences(
    rn_events: tuple[RomanEvent, ...],
    *,
    min_phrase_length: int = 2,
) -> tuple[Cadence, ...]:
    ...
```

Internally, a small state machine over the `rn_events`:
- Group consecutive events that share a key string.
- Within each group, iterate pairs/triples looking for V→I, V→vi, IV→I, iv→V patterns.
- Root-position detection: examine the `RomanEvent.numeral` string — any trailing digit other than `7` indicates inversion.

## Interaction with downstream stages

- `slide-designer` uses cadences to set phrase boundaries — one slide per phrase, anchored on the closing cadence.
- `music-theory-reviewer` agent validates cadence claims (e.g., "you said PAC but the V was inverted" — would require richer inversion data than we currently carry; note in output that our PAC/IAC distinction is soft).
- Annotated-PDF generator draws cadence brackets at `(start_measure, end_measure)`.

# Feature spec — vlm-autocorrect

## Problem

`--verify` produces 64 specific disagreement reports on the milonga, each with a `suggested_fix` string and a confidence score. Reading them by hand is fine for one piece but doesn't scale, and the downstream MIDI/playalong is still using the uncorrected MusicXML.

We want to **automatically apply the high-confidence fixes** to the MusicXML before harmony analysis / MIDI rendering. This converts the pipeline from "OMR-quality output" to "OMR + VLM-corrected output."

## Approach: structured fixes, not free-text patches

A `suggested_fix` like `"Remove the tuplet from the E5 and F5 sixteenth notes"` is human-readable but ambiguous to apply mechanically. Two options:

1. **Parse the existing free-text fix strings** with regex/NLP to figure out what to change. Brittle.
2. **Re-prompt Gemini for structured fixes** — for each disagreement, ask for a `MeasureFix` with typed operations (`remove_tuplet`, `change_pitch`, `add_dot`, `remove_chord_member`, etc.). Reliable, but doubles the API cost.

Going with option (2) — make the verifier itself emit structured fixes alongside the free-text issue/fix description. Same call, just a richer schema. Apply only fixes whose `confidence >= MIN_AUTO_APPLY_CONFIDENCE` (default 0.95) and whose `op` is in our supported-operations list.

## Inputs

- All inputs from `--verify`, plus:
- `auto_apply: bool = False` — gates the actual write
- `min_confidence: float = 0.95` — floor for auto-apply
- `output_xml: Path | None = None` — write the corrected XML here. Default: alongside the source as `<stem>.corrected.xml`. **Never overwrite the source.**

## Outputs

`AutoCorrectionResult`:
- `original_xml: Path`
- `corrected_xml: Path | None` — None if no fixes applied (or `auto_apply=False`)
- `applied_fixes: tuple[AppliedFix, ...]`
- `skipped_fixes: tuple[SkippedFix, ...]` — with reason (low confidence, unsupported op, parse error, etc.)
- `verification: ScoreVerification` — the full report

## Supported operations (v1)

Each operation maps to a deterministic XML mutation. Conservative set — anything not on this list is reported but not applied.

| Operation        | Args                                    | What it does |
|------------------|-----------------------------------------|--------------|
| `remove_tuplet`  | measure, beat (optional), note_pitch    | Remove `<time-modification>` and `<notations><tuplet>` from matching note(s); restore raw `<duration>` |
| `change_pitch`   | measure, from_pitch, to_pitch           | Change pitch step/octave/alter for the matching note |
| `change_duration`| measure, note_pitch, new_type, new_duration | Update `<type>` and `<duration>` |
| `add_dot`        | measure, note_pitch, voice (optional)   | Add a `<dot/>` and adjust `<duration>` ×1.5 |
| `remove_dot`     | measure, note_pitch, voice (optional)   | Remove `<dot/>`, restore base duration |
| `remove_note`    | measure, note_pitch, voice (optional)   | Remove a `<note>` element (used for "hallucinated extra note") |
| `change_time_signature` | new_beats, new_beat_type, measure (optional, default first) | Update `<time>` |

Out of scope for v1 (too risky to apply without human review):
- `move_to_voice` — voice-reassignment is structural; would need a separate spec
- `add_note` — synthesizing a new note from a fix string is too error-prone
- `add_backup` / `add_forward` — MusicXML-internals; defer

## Algorithm

1. Run `verify_score` (existing). For each disagreement, *also* request a `structured_fix: list[MeasureFix]` field via an extended Pydantic schema.
2. Filter: keep fixes where `confidence >= min_confidence` AND `op in SUPPORTED_OPS`.
3. Apply fixes in measure order, then in document order within a measure.
4. Each fix mutation is wrapped in try/except — a single failure doesn't stop the rest.
5. Write the corrected XML to `output_xml`.
6. Return the result.

## Edge cases

- **Same note targeted by two fixes** → apply both in order; second may be a no-op if first changed state. Log.
- **No matching note for a fix** (pitch + measure don't find anything) → skipped with reason "no match."
- **MusicXML structure differs from what the fix assumed** (e.g. fix says "remove dot" but no dot exists) → skipped, logged.
- **`auto_apply=False`** → still produces the report, doesn't write the file.
- **Re-running on already-corrected XML** → fixes for things now correct become no-ops; that's fine.
- **Output path equals input** → `AutoCorrectionError` ("won't overwrite source").

## Test cases

### Unit (no API)

- `test_apply_remove_tuplet_strips_time_modification` — synthetic XML with a triplet, apply `remove_tuplet`, assert `<time-modification>` is gone and the note is now a plain 16th.
- `test_apply_change_pitch_updates_step_octave_alter` — change A3 → A4; assert `<octave>` updated.
- `test_apply_remove_dot_changes_duration` — note with `<dot/>` and dur 12 (when divisions=8) → no dot, dur 8.
- `test_apply_change_time_signature_first_measure` — measure 1 with time 4/4 → 2/4.
- `test_skip_low_confidence_fix` — confidence below threshold → skipped with reason.
- `test_skip_unsupported_op` — op `add_backup` → skipped with reason.
- `test_skip_when_pitch_not_found` — fix says "change pitch X to Y" in measure where X isn't present → skipped.
- `test_refuses_overwriting_source` — output_xml == input_xml → `AutoCorrectionError`.
- `test_no_apply_when_auto_apply_false` — fixes available, auto_apply=False → corrected_xml is None, report still populated.

### Integration (real API call, marked)

- `test_milonga_corrected_xml_has_fewer_tuplets` — full run on milonga page 1, auto_apply=True. Count `<time-modification>` elements before and after; corrected version has strictly fewer.
- `test_milonga_corrected_midi_has_no_implicit_triplets` — render MIDI from corrected XML; the duration totals per measure should match the time signature (no overflow from spurious triplets).

## Non-goals

- General-purpose MusicXML editor / interactive UI.
- Round-trip with the user (apply fix → re-verify → apply again).
- Voice reassignment, beat-level rhythm restructuring.
- Caching / memoization of Gemini responses.

## Design sketch

```python
# src/agentic_sheet_music/omr/vlm_autocorrect.py

class AutoCorrectionError(Exception): ...

@dataclass(frozen=True)
class AppliedFix:
    measure: int
    op: str
    description: str
    before: str  # short snippet of the affected XML before mutation
    after: str

@dataclass(frozen=True)
class SkippedFix:
    measure: int
    op: str
    reason: str
    confidence: float

@dataclass(frozen=True)
class AutoCorrectionResult:
    original_xml: Path
    corrected_xml: Path | None
    applied_fixes: tuple[AppliedFix, ...]
    skipped_fixes: tuple[SkippedFix, ...]
    verification: ScoreVerification

def autocorrect_score(
    *,
    source_pdf: Path,
    candidate_xml: Path,
    omr_book: Path,
    movement: int = 1,
    auto_apply: bool = False,
    min_confidence: float = 0.95,
    output_xml: Path | None = None,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
) -> AutoCorrectionResult: ...
```

CLI: extend `--verify` with `--auto-correct` (off by default — opt-in for the actual mutation) and `--correction-confidence` (default 0.95).

## Correctness stance

The integration test asserts a **measurable improvement**: the corrected XML has fewer triplet markers than the original, and the MIDI no longer overflows measures from spurious triplets. We do not assert the fixes are *all* musically correct (Gemini still hallucinates ~5% of the time per the public benchmarks), but we assert the pipeline becomes meaningfully *better*.

Per `.claude/rules/correctness.md`: this stage's correctness test must read the actual milonga output and verify a specific musical claim (fewer wrong rhythms), not just "ran without crashing."

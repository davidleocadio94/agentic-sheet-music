---
description: Strict correctness rules for this project. Read before declaring any analysis stage done.
---

# Correctness rules (strict)

A passing test suite is necessary but not sufficient. This file is the final gate before any pipeline stage ships.

## 1. Real-world correctness tests are mandatory

Every pipeline stage (key detection, chord extraction, roman-numeral, cadence, annotation, audio) must have **at least one test** that:

- Runs on `/Users/davidvillarreal/Documents/Music/ClassicalGuitar/milonga.pdf` (Cardoso, D minor) via the full upstream pipeline.
- Asserts a **specific, human-verified musical claim**. Not "produces ≥20 events." Not "more than half labeled." Specifics like:
  - key detection: "measures 1–57 are all in D minor (tonic) or F major (relative major). No region in a distant key unless confidence > 0.85."
  - roman: "measure 13 is V7 in D minor." (derived from a human reading of the score).
  - cadence: "the piece contains at least 6 PACs in D minor."

Tests that only assert "the pipeline produced output" are **smoke tests**, not correctness tests. Both are fine, but a stage with only smoke tests is not done.

## 2. Never rely on a single signal

Every stage must use at least two independent signals to disagree with each other:

| Stage            | Signal 1                       | Signal 2                                | Disagreement goes to...    |
|------------------|--------------------------------|-----------------------------------------|----------------------------|
| key detection    | KS windowed correlation        | MusicXML `<key>` + `<mode>`             | Ambiguity; surface both    |
| chord extraction | Measure-window pitch bucket    | Beat-strength weighting                 | Prefer stronger-beat bucket |
| roman-numeral    | Chord-tone-count scoring       | Secondary-dominant look-ahead           | Emit both as Ambiguity     |
| cadence          | Two-chord RN pattern           | Key-region boundary (no cross-key)      | Drop the cadence           |

If a future stage is built from a single heuristic, document *why* and add a correctness test that would have caught the heuristic's failure mode.

## 3. Read the output with a musician's eyes

Before calling a stage done:

1. Run the full pipeline on `milonga.pdf`.
2. Open the relevant slice of output (annotated PDF, CLI dump, etc.) alongside the source score.
3. Check: does the analysis match what a human musician would write? If not, the stage is not done. Find the root cause in the stage — not in a fix-up layer downstream.

Specific known-correct facts for the milonga:
- **Home key is D minor** throughout movement 1. The score declares `<fifths>-1</fifths><mode>minor</mode>`.
- There is **no real modulation** to B♭ major, G minor, A minor, or C major. Short tonicizations of those areas are passing events, not key changes.
- The piece is full of `V7 → i` perfect authentic cadences ending 4-measure phrases. At least one DC (deceptive cadence) exists.
- Opening measures arpeggiate **D minor (D-A-E-F)**, not B♭ major.

## 4. Never silently override explicit information

- The MusicXML `<key>` element declares the key signature. The `<mode>` element, when present, declares major or minor.
- If the heuristic (KS, etc.) disagrees with the declared key, emit an **Ambiguity** — do not pick one silently.
- If the MusicXML is wrong (OMR error), that's still an ambiguity the user needs to see.

## 5. Failure mode: "green tests, wrong output"

If a stage's tests pass but the real-world output is musically wrong, the root cause is always one of:

1. The tests don't cover the failure — **add a test that would have caught it, fail it, then fix the code.**
2. The stage depends on a single brittle heuristic — **add a second signal and make them disagree visibly.**
3. The heuristic is fundamentally wrong for the musical style — **research the right approach before coding.**

Never patch the symptom. Always find the root cause.

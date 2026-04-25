# PRD — agentic-sheet-music

## Problem

Musicians and students have sheet music (PDFs, scans, MusicXML) but want to *understand* it: what key is this in, what chords are moving, why does this progression sound the way it does — and they want to hear it played back while studying. Existing tools solve one piece (OMR, or analysis, or playback) but nothing stitches them into a learning experience.

## Users

- **Primary:** self-taught / intermediate musicians studying repertoire (classical guitar, jazz, piano).
- **Secondary:** music teachers building lesson materials.

## Goals

1. Accept a score in any common format (PDF, PNG/JPG, MusicXML, MIDI) and produce a reliable internal representation.
2. Identify harmony: key(s), chord per beat/measure, roman numerals, cadences, modulations, non-chord tones.
3. Generate an **educational slide deck** that walks through the piece section-by-section with annotations.
4. Provide **audio playback** (full piece + isolated chord-by-chord) so the user can hear what they're reading.
5. Surface *confidence* at every stage — never hide a bad OCR result behind a confident-looking analysis.

## Non-goals (for v1)

- Real-time performance following.
- Live-mic pitch detection.
- Composition / generation.
- Mobile app. (CLI + static HTML/PDF deck output is enough.)

## Success criteria

- On a curated test set of 20 short classical/jazz pieces (already-clean MusicXML): ≥95% roman-numeral agreement with a human theory annotator.
- On scanned PDFs: OMR round-trips to MusicXML with ≤5% note error rate on clean scores; errors are *flagged*, not hidden.
- Generated deck reads like a lesson, not a dump: ≤1 analytical idea per slide, worked example, audio cue button.
- End-to-end pipeline runs in <2 min for a 2-page score on a laptop.

## User flow (v1)

```
user: uv run analyze inputs/milonga.pdf
  ↓
[omr] → MusicXML + confidence report
  ↓
[harmony] → HarmonyAnalysis (key, chords, RN, cadences)
  ↓
[reviewer agent] sanity-checks the analysis
  ↓
[slides] → outputs/milonga/deck.html
[player] → outputs/milonga/audio/*.wav (full + per-section)
  ↓
opens deck in browser, user can click any chord to hear it
```

## Open questions

- Which OMR engine is actually reliable enough? (Prototype with `oemer`, benchmark `audiveris`.)
- How do we handle ambiguous harmony (e.g., a passing ♭VII that could be modal mixture or a secondary dominant)? → surface both with rationale.
- SoundFont licensing for bundled playback.

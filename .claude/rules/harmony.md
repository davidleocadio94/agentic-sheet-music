---
paths:
  - "src/agentic_sheet_music/harmony/**/*.py"
  - "tests/harmony/**/*.py"
  - "tests/fixtures/harmony/**"
---

# Harmony-module rules

- Every analytical function takes a `Score` (or a slice of one) and returns a new immutable object. No mutation.
- Every new rule (new cadence type, new chord quality, new RN handling) lands with a fixture MusicXML under `tests/fixtures/harmony/` + a pytest.
- Use `music21` for the engraving-level plumbing (pitch spelling, chord construction) but **never** trust its high-level analysis blindly — wrap it, validate it, and fall back to custom logic on disagreement.
- Confidence fields are mandatory. If you can't compute a real confidence, record `confidence=None` and document why — do not emit `1.0` as a placeholder.
- Ambiguities are first-class. A function that picks one reading when two are defensible must emit both into `HarmonyAnalysis.ambiguities`.

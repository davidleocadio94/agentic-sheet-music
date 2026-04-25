---
paths:
  - "src/agentic_sheet_music/omr/**/*.py"
  - "tests/omr/**/*.py"
---

# OMR-module rules

- Never swallow an OMR error. Catch, attach context (file path, page, bbox), re-raise or emit as a `ConfidenceReport`.
- `source_confidence` on the resulting `Score` is mandatory and must be computed — no hardcoded optimism.
- MusicXML is the only output format. If you reach for a JSON dict to "simplify," stop — downstream consumers expect MusicXML.
- External engines (`oemer`, `audiveris`) are called via subprocess with explicit timeouts. Never rely on their Python API without a timeout wrapper.
- Tests that depend on external OMR binaries must be marked `@pytest.mark.omr_binary` and skipped by default in CI — the unit suite must pass without them.

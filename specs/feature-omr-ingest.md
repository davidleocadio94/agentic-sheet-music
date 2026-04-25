# Feature spec — omr-ingest

## Problem

The pipeline needs a single entry point that accepts any supported score file and returns a validated `Score` object. Today there is no such function: the CLI stub accepts a path but does nothing. Harmony analysis, slide generation, and audio rendering all depend on a `Score` — nothing can be built until ingest exists.

## Inputs

- `path: Path` — a file that may be:
  - `.musicxml`, `.xml`, `.mxl` — MusicXML (compressed or not)
  - `.mid`, `.midi` — Standard MIDI file
  - `.pdf`, `.png`, `.jpg`, `.jpeg` — score image, requires OMR
- Optional `config: IngestConfig` for overrides (OMR engine, timeouts). Not in v1.

## Outputs

- `Score` (from `agentic_sheet_music.types`) with:
  - `musicxml_path` — the canonical MusicXML produced. For XML inputs this is the input itself (possibly after decompression to a temp file). For MIDI inputs we convert via `music21`. For image/PDF inputs, OMR produces this (stubbed in v1 to raise `OmrNotAvailable`).
  - `meta.title`, `meta.composer`, `meta.time_signature`, `meta.key_signature` parsed from the MusicXML.
  - `parts` populated from `<score-part>` entries.
  - `source_confidence`:
    - `1.0` if the input was MusicXML that passed schema validation
    - `0.9` if the input was MIDI (lossy but deterministic)
    - OMR-dependent for images (out of scope for v1 — raises)

## Edge cases

- **Path doesn't exist** → `FileNotFoundError` with the path.
- **Unsupported extension** → `UnsupportedScoreFormat` with the extension and the supported list.
- **Corrupt MusicXML** (unparseable) → `InvalidMusicXML` with a short reason. Don't let `music21`'s deep stack trace leak.
- **MusicXML with no parts** → valid `Score` but empty `parts` tuple; log a warning.
- **Compressed `.mxl`** → transparently decompress to a temp MusicXML file.
- **UTF-8 BOM** in MusicXML → handle cleanly.
- **OMR inputs (PDF/image)** → raise `OmrNotAvailable` in v1. Wired up in a later feature spec.

## Test cases

Fixtures in `tests/fixtures/omr-ingest/`:

1. `c-major-scale.musicxml` — minimal valid MusicXML, one part, one measure. Happy path.
2. `two-part.musicxml` — two `<score-part>` entries. Asserts parts tuple has length 2.
3. `no-parts.musicxml` — valid XML but no parts. Asserts `Score` with empty parts.
4. `corrupt.musicxml` — malformed XML. Asserts `InvalidMusicXML` raised.
5. `c-major.mid` — trivial MIDI (added in a later iteration; v1 test can skip with a marker).

Tests in `tests/omr/test_ingest.py`:

- `test_ingest_musicxml_returns_score` — fixture 1 → `Score` with confidence 1.0.
- `test_ingest_counts_parts` — fixture 2 → `len(score.parts) == 2`.
- `test_ingest_handles_no_parts` — fixture 3 → `Score` with empty parts, no exception.
- `test_ingest_corrupt_xml_raises` — fixture 4 → `InvalidMusicXML`.
- `test_ingest_missing_file_raises` — nonexistent path → `FileNotFoundError`.
- `test_ingest_unsupported_extension_raises` — `.txt` file → `UnsupportedScoreFormat`.
- `test_ingest_pdf_raises_omr_not_available` — any `.pdf` → `OmrNotAvailable`.

Happy-path fixture for the initial RED test: `c-major-scale.musicxml`.

## Non-goals

- OMR implementation (images/PDFs) — separate feature spec.
- MIDI ingest — covered by a separate spec in the next iteration. v1 raises `OmrNotAvailable` equivalents for MIDI too (`MidiIngestNotImplemented`).
- Schema validation against the MusicXML DTD — `music21`'s tolerant parse is sufficient for v1; strict validation is a follow-up.
- Input sanitization against XXE / billion-laughs — N/A for v1 because inputs are local user files under the user's control; note this decision here so a future reviewer doesn't miss it. If we ever accept uploads, revisit.

## Design sketch

```python
# src/agentic_sheet_music/omr/ingest.py

class IngestError(Exception): ...
class UnsupportedScoreFormat(IngestError): ...
class InvalidMusicXML(IngestError): ...
class OmrNotAvailable(IngestError): ...
class MidiIngestNotImplemented(IngestError): ...

SUPPORTED = {".musicxml", ".xml", ".mxl", ".mid", ".midi", ".pdf", ".png", ".jpg", ".jpeg"}

def ingest(path: Path) -> Score:
    ...
```

Internally:
- `_load_musicxml(path) -> Score` — uses `music21.converter.parse`, extracts meta + parts, sets confidence 1.0.
- `_load_midi(path) -> Score` — raises `MidiIngestNotImplemented` for v1.
- `_load_image(path) -> Score` — raises `OmrNotAvailable` for v1.

All branches return the same `Score` shape; only `source_confidence` and `musicxml_path` differ.

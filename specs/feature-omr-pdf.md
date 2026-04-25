# Feature spec — omr-pdf

## Problem

`ingest()` currently raises `OmrNotAvailable` for PDFs. The whole pipeline is unusable on the primary input format (sheet music PDFs). We need a real OMR stage that converts a PDF to MusicXML and feeds it back into the existing ingest path.

## Approach

Shell out to **Audiveris 5.10.2** (`/Applications/Audiveris.app/Contents/MacOS/Audiveris`) in batch mode with `-export`. Parse its output directory, locate the `.mxl` file(s) it produces, decompress them to plain MusicXML, and dispatch to the existing `_load_musicxml()` path.

Audiveris already handles PDF rasterization internally, so no `pdftoppm` step is needed.

## Inputs

- `path: Path` — a PDF file.
- `output_dir: Path | None` — where Audiveris writes its artifacts. Defaults to a fresh temp dir per call.
- `timeout_seconds: int = 600` — max CLI runtime (Audiveris is CPU-heavy; 6-page scores take ~30s but complex scores can take minutes).
- `audiveris_binary: Path | None` — override the binary location. Defaults to `/Applications/Audiveris.app/Contents/MacOS/Audiveris`.

## Outputs

- `Score` from the existing pipeline. Additionally:
  - `source_confidence` set to **0.7** by default for Audiveris output. Rationale: typical OMR error rate on clean engraved PDFs is a few percent per measure, and we don't want downstream stages to treat the output as ground truth. A future feature will derive a real confidence from Audiveris log warnings.
  - If Audiveris produces multiple "movements" (it splits at large page gaps — it split our `milonga.pdf` into pp.1–3 and pp.4–6), we return the **first** movement's Score and emit a log warning naming the others. A future spec will address multi-movement handling.

## Edge cases

- **Audiveris binary missing** → `AudiverisNotInstalled` with install instructions.
- **PDF is malformed / Audiveris crashes** → `OmrFailed` with the tail of Audiveris's stderr (the raw log is huge; capture last ~20 lines).
- **Timeout exceeded** → `OmrTimeout`, kill the process.
- **No `.mxl` produced** (Audiveris ran but couldn't find any staves) → `OmrEmpty`.
- **Multi-page PDF with mixed content** (text pages + score pages) — Audiveris handles this; we don't pre-filter.

## Test cases

- `test_omr_binary_missing_raises` — pass a nonexistent binary path, expect `AudiverisNotInstalled`. **Runs without Audiveris.**
- `test_pdf_converts_to_score[audiveris]` — marked `@pytest.mark.omr_binary`, run against a small rendered fixture PDF (or skip if no Audiveris). Assert `Score` with `source_confidence == 0.7` and at least one note-bearing measure.
- `test_ingest_pdf_dispatches_to_omr[audiveris]` — marked, integration-level: `ingest(some.pdf)` produces a `Score` without raising `OmrNotAvailable`.

The `omr_binary` marker is already declared in `pyproject.toml`; tests using it are skipped by default unless invoked with `pytest -m omr_binary`.

Fixture PDF: reuse `milonga.pdf` (full real-world test) for the integration smoke test, gated by file existence so it doesn't break CI if the user relocates it. A minimal rendered fixture PDF is a future task.

## Non-goals

- Multi-movement handling beyond "take the first." Deferred.
- Confidence derived from Audiveris log warnings. Deferred.
- Alternative OMR backends (`homr`, `oemer`). Deferred — the architecture allows swapping via `pdf_to_musicxml.convert()` but only Audiveris is implemented in v1.
- Vision-LLM verification pass. Separate spec.

## Design sketch

```python
# src/agentic_sheet_music/omr/pdf_to_musicxml.py

AUDIVERIS_DEFAULT = Path("/Applications/Audiveris.app/Contents/MacOS/Audiveris")

class OmrFailed(Exception): ...
class AudiverisNotInstalled(OmrFailed): ...
class OmrTimeout(OmrFailed): ...
class OmrEmpty(OmrFailed): ...

def pdf_to_musicxml(
    pdf: Path,
    *,
    output_dir: Path | None = None,
    timeout_seconds: int = 600,
    audiveris_binary: Path = AUDIVERIS_DEFAULT,
) -> Path:
    """Convert a PDF to MusicXML via Audiveris. Returns the plain .xml path."""
```

And in `ingest.py`:
```python
def _load_pdf(path: Path) -> Score:
    musicxml = pdf_to_musicxml(path)
    score = _load_musicxml(musicxml)
    # Override confidence — OMR output is not ground truth.
    return replace(score, source_confidence=0.7)
```

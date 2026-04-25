# Feature spec — annotated-pdf

## Problem

The user's primary deliverable is **their original PDF score with analytical markup layered on top** — roman numerals under each measure, cadence brackets, key-region banners. Not a re-engraved companion (easier but loses the "my score with marks on it" experience).

To draw on the original PDF, we need **per-measure page coordinates**. Audiveris's MusicXML export doesn't carry them, but its `.omr` book file (a ZIP of XML) does. This spec covers:

1. Extracting measure bounding boxes from the `.omr` file.
2. Drawing annotations onto a copy of the original PDF via PyMuPDF.

**Hard constraint:** never overwrite the source PDF. Always write to a new file under `outputs/<piece>/`.

## Inputs

- `source_pdf: Path` — the original score PDF (treated read-only).
- `omr_book: Path` — the `.omr` file Audiveris produced for this PDF.
- `analysis: HarmonyAnalysis` — the `RomanEvent`s, `Cadence`s, and `KeyRegion`s to draw.
- `output_pdf: Path` — destination for the annotated copy. Must not equal `source_pdf`.
- `movement: int = 1` — Audiveris splits PDFs into movements; `.omr` groups sheets into movements via `movement-start="true"` on the first sheet. We annotate measures belonging to the named movement only.

## Outputs

- A new PDF at `output_pdf`, visually identical to `source_pdf` except:
  - Below each annotated measure: the roman numeral (small, right-aligned under the bar), in a contrasting color per key region.
  - At each cadence: a bracket spanning `start_measure..end_measure` with the cadence kind ("PAC", "DC", etc.) above it.
  - At the start of each key region: a thin horizontal banner with the key name (e.g. "D minor — m.9"), in the region's color.
- Returns `Path` to the written file. Raises on any error.

## Coordinate pipeline

Per sheet in the `.omr`:
1. Parse `sheet#N/sheet#N.xml`.
2. For each `<system>`:
   - Walk `<part>/<staff>/<lines>/<line>` — each line has two `<point x y>` endpoints. The staff top is `min(y)` and bottom is `max(y)` across the 5 lines.
3. For each `<stack>` (= one measure in that system):
   - `left`, `right` give the x-range in sheet pixels.
   - Combine with the enclosing system's staff y-range to get `(x0, y0, x1, y1)` in sheet pixels.
4. Assign a global measure number by walking sheets in order within the chosen movement, starting at 1 and incrementing per stack.

Per sheet pixels → PDF points:
- The `.omr` pictures are rendered at a known density: the top-level `<picture width=… height=…>` is the sheet image in pixels, and the source PDF page's width/height in points (from PyMuPDF) gives us scale factors `sx = pdf_w / pic_w`, `sy = pdf_h / pic_h`. We apply those.
- Sheets in `.omr` correspond 1:1 to source-PDF pages (order preserved).

## Drawing

PyMuPDF (`import pymupdf`) — open `source_pdf`, iterate pages, draw text + shapes, save to `output_pdf`. Never call `.save()` with `incremental=True` on the original path; always save to a fresh filename.

Style:
- Roman numerals: 8pt sans-serif, placed ~12 pt below the staff bottom, horizontally centered under the measure's x-range (or at `beat` offset if we want beat-level placement — v1 does one label per measure at the downbeat).
- Cadence brackets: a thin rounded line above the staff top, spanning `(start_measure.x0, end_measure.x1)`, with the label text above it.
- Key-region banner: a 2pt-tall colored rectangle directly above the first measure of the region, spanning the measure's x-range; label text ("D minor") above the rectangle.
- Colors: a small palette of high-contrast colors, cycled per distinct key. Cadences use a single accent color regardless of key.

## Edge cases

- **Output path equals source path** → raise `AnnotationOutputError("output_pdf must differ from source_pdf")`.
- **Output path already exists** → **do NOT overwrite.** Raise `AnnotationOutputError` with "file exists; choose a different path or delete the existing file." (Per user constraint: always keep a clean original; never clobber derivatives silently either.)
- **`.omr` missing or corrupt** → `OmrBookParseError` with path.
- **Movement number out of range** → `ValueError`.
- **Measure in the analysis doesn't appear in `.omr` coordinates** → skip that annotation, log a warning. Don't crash the whole render for one missing measure.
- **Two systems on the same page** (normal classical-guitar layout — 5–8 systems per page) → handled naturally; each `<system>` has its own y-range.
- **Cadence spans a page break** → draw one bracket ending at the right edge of the first page's last system, another starting at the second page's first system. For v1, just draw the end-measure's single-measure bracket if start and end are on different pages and log a note.

## Test cases

Tests in `tests/annotation/test_annotated_pdf.py`:

Unit tests on the coordinate parser — pure .omr → dict, no PDF needed:
- `test_parses_measure_boxes_from_omr` — use a tiny sample `.omr` (we'll add a fixture — or skip gracefully if the real milonga `.omr` path isn't present). Assert that at least one measure returns a sensible bounding box.
- `test_respects_movement_boundary` — in the milonga, measure 1 of movement 2 starts on sheet 4, not sheet 1. Assert global measure numbering restarts at the movement boundary.

Integration tests (marked `@pytest.mark.omr_binary` since they need the real Audiveris output):
- `test_annotate_milonga_end_to_end` — runs full pipeline, writes to `tmp_path / "annotated.pdf"`, asserts the file exists and is a non-empty PDF (>10 KB), and that the source file is unchanged (hash compared pre/post).
- `test_refuses_to_overwrite_source` — `output_pdf == source_pdf` → `AnnotationOutputError`.
- `test_refuses_to_overwrite_existing_output` — `output_pdf` points to an existing file → `AnnotationOutputError`.

## Non-goals

- Beat-level annotation placement within a measure. v1 is one label per measure at the downbeat; beat-placement is a follow-up once we trust the measure placement.
- Voice-specific annotations (two-voice stems labeled separately). v1 puts everything under the whole measure.
- Page-break handling for brackets (see edge case above).
- Editing existing annotations or regenerating incrementally. If the user wants new output, they delete the old file or pass a new path.
- Non-Audiveris OMR backends. `.omr` is Audiveris-specific; if we add another backend later, that backend must provide its own coordinate source.
- Facsimile annotation for scanned-handwritten PDFs. Audiveris's quality degrades there and so does coordinate accuracy.

## Design sketch

```python
# src/agentic_sheet_music/slides/annotate.py
# (the name "slides" predates this feature; keeping it as the "annotation" module.
#  Will rename to src/agentic_sheet_music/annotate/ if more modules land here.)

@dataclass(frozen=True)
class MeasureBox:
    measure: int            # global measure number in the movement
    page_index: int         # 0-based page index in the source PDF
    x0: float               # PDF points
    y0: float
    x1: float
    y1: float
    staff_bottom: float     # convenience: where to anchor annotations below

class OmrBookParseError(Exception): ...
class AnnotationOutputError(Exception): ...

def parse_measure_boxes(
    omr_book: Path,
    source_pdf: Path,
    movement: int = 1,
) -> dict[int, MeasureBox]: ...

def annotate_pdf(
    source_pdf: Path,
    omr_book: Path,
    analysis: HarmonyAnalysis,
    output_pdf: Path,
    *,
    movement: int = 1,
) -> Path: ...
```

`HarmonyAnalysis` (from `types.py`) already bundles `score`, `key_regions`, `chords`, `roman_numerals`, `cadences`, `ambiguities`. The rest of the pipeline will get a small helper that constructs one from the outputs of the existing stages:

```python
# src/agentic_sheet_music/harmony/__init__.py
def build_analysis(score, key_regions, chords, roman_numerals, cadences, ambiguities) -> HarmonyAnalysis:
    ...
```

## Risk

The coordinate pipeline is the ~80% risk of this spec. If the `.omr` schema is wildly different for movements 2+ of a multi-movement PDF, we'll need iteration. The milonga is the only repertoire we've tested; plan to add other pieces (a Bach prelude, a short Tárrega) to the fixture set before claiming v1 is stable.

The annotations will *look ugly* at first pass — classical-guitar engravings are dense, and there may not be enough vertical space between systems for clean labels. v1 ships functional (correct positioning) not beautiful (typographic polish).

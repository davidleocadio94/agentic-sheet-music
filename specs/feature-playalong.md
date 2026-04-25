# Feature spec — playalong

## Problem

MIDI-on-disk isn't the experience David wants. He wants a **playalong interface**: open a page, see the annotated score, click a measure, hear it play from there with the currently-playing measure highlighted.

## Deliverable

A self-contained static site under `outputs/<piece>/playalong/`:
```
playalong/
  index.html                ← the page
  score-page-1.png          ← rendered annotated pages
  score-page-2.png
  ...
  score.mid                 ← full-piece MIDI (all movements stitched? see below)
  measures.json             ← [{measure, page, x0, y0, x1, y1, onset_seconds}]
  vendor/
    spessasynth_lib.js      ← MIDI sequencer (~72 KB)
    spessasynth_core/*      ← engine + worklet processor
    piano.sf3               ← compressed piano soundfont (~1.5–3 MB)
  serve.sh                  ← `python3 -m http.server 8080` helper for Safari
```

Opening `index.html` directly in Chrome/Firefox plays. Safari blocks AudioWorklet from `file://`; `serve.sh` + `http://localhost:8080` is the workaround for Safari users.

## Interaction

- Page shows all rendered annotated pages stacked vertically.
- Hovering a measure shows a subtle highlight overlay.
- Click a measure → playback starts at that measure, continues to end of piece. Space bar pauses/resumes. Esc stops.
- During playback, the currently-playing measure gets a stronger highlight (semi-transparent color box).
- A dropdown at the top switches between movements (guitar 1 / guitar 2 for the milonga).
- When switching movements, any playback stops.

## Inputs

- `source_pdf: Path` — original PDF
- `omr_book: Path` — Audiveris `.omr` book (needed for measure coordinates)
- `analyses: tuple[HarmonyAnalysis, ...]` — one per movement
- `midi_paths: tuple[Path, ...]` — one MIDI per movement (produced by the existing audio stage)
- `output_dir: Path` — where `playalong/` lives. Created; existing content overwritten (it's all derivative).

## Outputs

- `Path` to the `index.html`.

## Algorithm

1. Render each source PDF page to PNG (150 DPI is enough for on-screen), with annotations already drawn — we render the already-annotated PDF, not the source.
2. For each movement, build `measures.json`:
   - Measure boxes from `parse_measure_boxes()` in PDF points → convert to PNG pixel coordinates via the same scale factor used during rendering.
   - MIDI onset seconds per measure: parse the MIDI we wrote; walk tempo events; compute absolute seconds for each measure's downbeat.
3. Copy vendor JS + SoundFont into `playalong/vendor/`.
4. Write `index.html` from a template string. The HTML references all local files with relative paths.

## Vendor assets

Not checked into git; a one-time download fetched by the build (`requests` already in deps via music21's transitive tree — confirm). Cache under `~/.cache/agentic-sheet-music/vendor/` to avoid re-downloading per piece.

Minimal list:
- `spessasynth_lib.js` (ESM bundle, pinned version)
- `piano.sf3` — a small public-domain GM piano. Start with a known-good source and document the URL in the spec; user can override by placing their own at the cache path.

## Correctness tests

Tests in `tests/playalong/test_build.py`:

- `test_builds_static_site_for_block_chords` — happy path with a fixture score. Assert every expected file exists, index.html references them with relative paths, `measures.json` is valid JSON with N entries.
- `test_measure_timings_monotonic` — for a simple 4-measure fixture, each successive measure's onset_seconds must strictly increase.
- `test_refuses_missing_omr_book` — clear error.
- `test_vendor_assets_present` — once cached, subsequent builds don't re-download. For CI, a `VENDOR_CACHE_DIR` env var lets tests point at a pre-populated cache.
- **Real-world** (`@pytest.mark.omr_binary`): build on the milonga (both movements), assert `outputs/milonga/playalong/measures.json` contains entries for all 57 + ~57 measures, onset_seconds strictly increase within each movement, and `index.html` references both pages 1–3 and 4–6 PNGs.

## Non-goals

- Server-based deployment / hosting. Local-file only.
- Audio effects, mixing, reverb, tempo override in the UI. Ship-stock MIDI → piano → speakers.
- Highlighting sub-measure beats. Measure-level resolution only.
- Recording the user playing along and syncing. Way out of scope.
- iOS Safari — the `file://` + AudioWorklet combination is brittle there. Document as known-iffy; the build still works if the user serves over HTTP.

## Design sketch

```python
# src/agentic_sheet_music/playalong/build.py

def build_playalong(
    source_pdf: Path,
    annotated_pdf: Path,
    omr_book: Path,
    analyses: tuple[HarmonyAnalysis, ...],
    midi_paths: tuple[Path, ...],
    output_dir: Path,
    *,
    vendor_cache: Path | None = None,
) -> Path: ...
```

CLI integration: a new `--playalong` flag that implies `--annotate` and `--audio`. Default output: `outputs/<stem>/playalong/`.

## Risk

**Browser playback across file:// is the fragile piece.** Chrome + Firefox work; Safari blocks AudioWorklet imports from `file://`. The `serve.sh` helper is the contingency. If on testing even Chrome/Firefox misbehave, we'll switch from the Worklet-based synth to a Worker-based fallback that SpessaSynth supports — but that's a v2 concern. v1 ships working in Chrome + Firefox with a documented Safari workaround.

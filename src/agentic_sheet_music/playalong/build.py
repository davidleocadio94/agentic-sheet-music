"""Build a static playalong site. See specs/feature-playalong.md.

Output layout:
    playalong/
      index.html
      pages/page-N.png         (annotated source pages, one PNG each)
      midi/mvtN.mid            (one MIDI per movement)
      measures-mvtN.json       (one JSON per movement with box + onset_seconds)
      vendor/spessasynth_lib.js, piano.sf3, ...
      serve.sh                 (Safari helper)
"""

from __future__ import annotations

import json
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from music21 import midi as m21midi

from agentic_sheet_music.annotate.pdf import parse_measure_boxes
from agentic_sheet_music.types import HarmonyAnalysis

DEFAULT_DPI = 150
DEFAULT_VENDOR_CACHE = Path.home() / ".cache" / "agentic-sheet-music" / "vendor"

REQUIRED_VENDOR_FILES = (
    "spessasynth_lib.js",
    "spessasynth_core.js",
    "spessasynth_processor.js",
    "piano.sf3",
)


class PlayalongBuildError(Exception):
    pass


@dataclass(frozen=True)
class _MeasureEntry:
    measure: int
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    onset_seconds: float

    def to_dict(self) -> dict:
        return {
            "measure": self.measure,
            "page": self.page,
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
            "onset_seconds": self.onset_seconds,
        }


def build_playalong(
    *,
    source_pdf: Path,
    annotated_pdf: Path,
    omr_book: Path,
    analyses: tuple[HarmonyAnalysis, ...],
    midi_paths: tuple[Path, ...],
    output_dir: Path,
    vendor_cache: Path | None = None,
    dpi: int = DEFAULT_DPI,
) -> Path:
    """Build a static playalong site. Returns the path to `index.html`."""
    if not omr_book.exists():
        raise PlayalongBuildError(f"omr_book not found: {omr_book}")
    if len(analyses) != len(midi_paths):
        raise PlayalongBuildError(
            "analyses and midi_paths must have the same length"
        )
    if not analyses:
        raise PlayalongBuildError("at least one movement required")

    vendor = _resolve_vendor(vendor_cache)
    output_dir.mkdir(parents=True, exist_ok=True)

    _render_pdf_pages(annotated_pdf, output_dir / "pages", dpi)
    _copy_vendor(vendor, output_dir / "vendor")

    midi_dir = output_dir / "midi"
    midi_dir.mkdir(exist_ok=True)
    for i, mid in enumerate(midi_paths):
        dest = midi_dir / f"mvt{i + 1}.mid"
        shutil.copyfile(mid, dest)

    # Per-movement measures.json — combines sheet coordinates with MIDI timings.
    for i, (analysis, midi_path) in enumerate(zip(analyses, midi_paths, strict=True)):
        movement_number = i + 1
        boxes = parse_measure_boxes(omr_book, source_pdf, movement=movement_number)
        onsets = _measure_onsets_seconds(midi_path, sorted(boxes))
        entries = _build_entries(boxes, onsets, annotated_pdf, dpi)
        (output_dir / f"measures-mvt{movement_number}.json").write_text(
            json.dumps([e.to_dict() for e in entries], indent=2)
        )

    (output_dir / "index.html").write_text(_render_index_html(len(analyses)))
    _write_serve_script(output_dir / "serve.sh")
    return output_dir / "index.html"


# ---------------------------------------------------------------------------
# MIDI → measure onset times


def _measure_onsets_seconds(midi_path: Path, measures: list[int]) -> dict[int, float]:
    """Walk MIDI tempo events and find the elapsed seconds at each measure's
    downbeat. Assumes 4/4 if no time signature events are found (music21's
    default). For a multi-meter score the MIDI itself carries the correct tempo
    and delta timings, so tick-to-second conversion is exact regardless.
    """
    mf = m21midi.MidiFile()
    mf.open(str(midi_path))
    try:
        mf.read()
    finally:
        mf.close()

    ticks_per_quarter = mf.ticksPerQuarterNote or 480

    # Collect tempo changes: [(absolute_tick, microseconds_per_quarter)].
    tempo_changes: list[tuple[int, int]] = [(0, 500_000)]  # MIDI default = 120 BPM
    for track in mf.tracks:
        abs_tick = 0
        for ev in track.events:
            abs_tick += ev.time or 0
            if ev.type == m21midi.MetaEvents.SET_TEMPO:
                us_per_quarter = int.from_bytes(ev.data, "big")
                tempo_changes.append((abs_tick, us_per_quarter))
    tempo_changes.sort(key=lambda t: t[0])
    # Deduplicate same-tick entries keeping the last.
    deduped: list[tuple[int, int]] = []
    for tick, us in tempo_changes:
        if deduped and deduped[-1][0] == tick:
            deduped[-1] = (tick, us)
        else:
            deduped.append((tick, us))
    tempo_changes = deduped

    # Compute seconds-per-tick helper over the piecewise tempo map.
    def seconds_at(tick: int) -> float:
        total = 0.0
        for i, (start_tick, us_per_quarter) in enumerate(tempo_changes):
            next_tick = (
                tempo_changes[i + 1][0] if i + 1 < len(tempo_changes) else tick
            )
            segment_start = start_tick
            segment_end = min(next_tick, tick)
            if segment_end <= segment_start:
                continue
            delta_ticks = segment_end - segment_start
            seconds_per_tick = (us_per_quarter / 1_000_000.0) / ticks_per_quarter
            total += delta_ticks * seconds_per_tick
            if tick <= next_tick:
                break
        return total

    # Collect each measure-start tick by scanning NOTE_ON events in the first
    # track that has notes; we use music21 to re-parse via converter to get
    # measure-aware offsets instead — more reliable.
    from music21 import converter

    stream = converter.parse(str(midi_path))
    # music21's measure numbers in an imported MIDI may start at 0; normalize.
    onsets: dict[int, float] = {}
    ticks_per_measure_hint = ticks_per_quarter * 4  # fallback for 4/4

    # Best source: the imported stream's measures, with quarter-length offsets.
    measure_offsets = {}
    for m in stream.recurse().getElementsByClass("Measure"):
        num = getattr(m, "number", None)
        if num is None:
            continue
        measure_offsets[int(num)] = float(m.offset)

    if measure_offsets:
        # Offsets are in quarterLengths; convert to ticks then seconds.
        for num in measures:
            if num not in measure_offsets:
                continue
            tick = int(round(measure_offsets[num] * ticks_per_quarter))
            onsets[num] = seconds_at(tick)
    else:
        # Fall back to assuming uniform measure length.
        for num in measures:
            tick = (num - 1) * ticks_per_measure_hint
            onsets[num] = seconds_at(tick)

    return onsets


# ---------------------------------------------------------------------------
# Bounding boxes → page-pixel coordinates


def _build_entries(
    boxes: dict[int, "MeasureBox"],  # noqa: F821 — forward ref to annotate module type
    onsets: dict[int, float],
    annotated_pdf: Path,
    dpi: int,
) -> list[_MeasureEntry]:
    # PDF-point → PNG-pixel conversion ratio is dpi/72 (since 1 point = 1/72 inch).
    scale = dpi / 72.0
    entries: list[_MeasureEntry] = []
    for num in sorted(boxes):
        box = boxes[num]
        onset = onsets.get(num)
        if onset is None:
            continue
        entries.append(
            _MeasureEntry(
                measure=num,
                page=box.page_index,
                x0=box.x0 * scale,
                y0=box.y0 * scale,
                x1=box.x1 * scale,
                y1=box.y1 * scale,
                onset_seconds=onset,
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Rendering + copying


def _render_pdf_pages(pdf_path: Path, dest_dir: Path, dpi: int) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            pix.save(dest_dir / f"page-{i + 1}.png")


def _resolve_vendor(override: Path | None) -> Path:
    cache = override or DEFAULT_VENDOR_CACHE
    if not cache.exists():
        raise PlayalongBuildError(
            f"vendor cache {cache} does not exist. "
            "Run `uv run python -m agentic_sheet_music.playalong.fetch_vendor` "
            "to populate it, or pass a pre-populated --vendor-cache path."
        )
    missing = [f for f in REQUIRED_VENDOR_FILES if not (cache / f).exists()]
    if missing:
        raise PlayalongBuildError(f"vendor cache {cache} missing: {missing}")
    return cache


def _copy_vendor(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.is_dir():
            shutil.copytree(item, dest / item.name, dirs_exist_ok=True)
        else:
            shutil.copyfile(item, dest / item.name)


def _write_serve_script(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "# Safari blocks AudioWorklet imports from file:// URLs. Run this to\n"
        "# serve the playalong site over http://localhost:8080 instead.\n"
        'cd "$(dirname "$0")" && python3 -m http.server 8080\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ---------------------------------------------------------------------------
# HTML template


def _render_index_html(num_movements: int) -> str:
    movement_options = "\n".join(
        f'      <option value="{i + 1}">Movement {i + 1}</option>'
        for i in range(num_movements)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Playalong</title>
<style>
  body {{ margin: 0; font-family: -apple-system, sans-serif; background: #1a1a1a; color: #eee; }}
  header {{ padding: 8px 16px; background: #2a2a2a; display: flex; align-items: center; gap: 12px; position: sticky; top: 0; z-index: 10; }}
  header h1 {{ margin: 0; font-size: 16px; font-weight: 500; }}
  header select, header button {{ background: #333; color: #eee; border: 1px solid #555; padding: 4px 10px; border-radius: 4px; }}
  header button {{ cursor: pointer; }}
  header button:hover {{ background: #444; }}
  #status {{ margin-left: auto; font-size: 12px; color: #aaa; }}
  #error-banner {{ background: #8a2020; color: white; padding: 10px 16px; font-size: 13px; line-height: 1.5; display: none; }}
  #error-banner code {{ background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 3px; }}
  #pages {{ display: flex; flex-direction: column; align-items: center; gap: 16px; padding: 16px; }}
  .page-wrap {{ position: relative; display: inline-block; }}
  .page-wrap img {{ display: block; max-width: 100%; }}
  .measure-overlay {{ position: absolute; cursor: pointer; background: rgba(0,0,0,0); transition: background 0.1s; }}
  .measure-overlay:hover {{ background: rgba(255, 200, 50, 0.2); }}
  .measure-overlay.playing {{ background: rgba(255, 100, 50, 0.35); }}
</style>
</head>
<body>
<header>
  <h1>Playalong</h1>
  <select id="movement">
{movement_options}
  </select>
  <button id="play">▶ Play from start</button>
  <button id="stop">◼ Stop</button>
  <label style="display:flex;align-items:center;gap:6px;font-size:12px;">
    Tempo
    <input id="tempo" type="range" min="0.25" max="1.75" step="0.05" value="1.0" style="width:140px;">
    <span id="tempo-value" style="width:3em;text-align:right;">1.00x</span>
  </label>
  <span id="status">Click a measure to play from there.</span>
</header>
<div id="error-banner"></div>
<div id="pages"></div>

<script type="importmap">
{{
  "imports": {{
    "spessasynth_core": "./vendor/spessasynth_core.js"
  }}
}}
</script>
<script type="module">
import {{ WorkletSynthesizer, Sequencer }} from "./vendor/spessasynth_lib.js";

const PAGES_DIR = "./pages";
const MIDI_DIR = "./midi";
const SF_URL = "./vendor/piano.sf3";
const WORKLET_URL = "./vendor/spessasynth_processor.js";

const movementSel = document.getElementById("movement");
const playBtn = document.getElementById("play");
const stopBtn = document.getElementById("stop");
const tempoSlider = document.getElementById("tempo");
const tempoLabel = document.getElementById("tempo-value");
const statusEl = document.getElementById("status");
const pagesEl = document.getElementById("pages");
const errorEl = document.getElementById("error-banner");
let tempoRate = 1.0;

function showError(msg, err) {{
  console.error(msg, err);
  const detail = err ? (err.message || String(err)) : "";
  errorEl.innerHTML = "<strong>" + msg + "</strong>" +
    (detail ? "<br><code>" + detail + "</code>" : "") +
    "<br><small>If you opened this file directly, try Chrome/Firefox. " +
    "Safari blocks AudioWorklet from file:// — run <code>bash serve.sh</code> " +
    "in this folder and open http://localhost:8080/ instead.</small>";
  errorEl.style.display = "block";
}}
window.addEventListener("error", (ev) => showError("Script error", ev.error || ev.message));
window.addEventListener("unhandledrejection", (ev) => showError("Async error", ev.reason));

let ctx, synth, seq;
let measures = [];
let overlayNodes = new Map();
let rafId = null;
let numPages = 0;

async function ensureAudio() {{
  if (ctx) return;
  try {{
    ctx = new AudioContext();
    await ctx.audioWorklet.addModule(WORKLET_URL);
    synth = new WorkletSynthesizer(ctx);
    synth.connect(ctx.destination);
    const sf = await (await fetch(SF_URL)).arrayBuffer();
    await synth.soundBankManager.addSoundBank(sf, "main");
    statusEl.textContent = "Ready. Click a measure.";
  }} catch (err) {{
    ctx = null; synth = null;
    showError("Could not initialize audio engine", err);
    throw err;
  }}
}}

async function loadMovement(mvt) {{
  stop();
  statusEl.textContent = `Loading movement ${{mvt}}…`;
  const measuresData = await (await fetch(`./measures-mvt${{mvt}}.json`)).json();
  measures = measuresData;
  numPages = Math.max(...measures.map(m => m.page)) + 1;
  await renderPages();
  renderOverlays();
  statusEl.textContent = "Click a measure to play from there.";
}}

async function renderPages() {{
  pagesEl.innerHTML = "";
  overlayNodes.clear();
  // Render enough pages to cover the highest page_index this movement references.
  const pagesInUse = new Set(measures.map(m => m.page));
  const pageList = [...pagesInUse].sort((a, b) => a - b);
  for (const pageIdx of pageList) {{
    const wrap = document.createElement("div");
    wrap.className = "page-wrap";
    wrap.dataset.page = String(pageIdx);
    const img = document.createElement("img");
    img.src = `${{PAGES_DIR}}/page-${{pageIdx + 1}}.png`;
    await new Promise((resolve) => {{ img.onload = resolve; img.onerror = resolve; }});
    wrap.appendChild(img);
    pagesEl.appendChild(wrap);
  }}
}}

function renderOverlays() {{
  for (const m of measures) {{
    const wrap = pagesEl.querySelector(`.page-wrap[data-page="${{m.page}}"]`);
    if (!wrap) continue;
    const img = wrap.querySelector("img");
    // Scale from the measures.json pixel space (rendered at build DPI) to the
    // image's actual displayed size (responsive).
    const sx = img.clientWidth / img.naturalWidth;
    const sy = img.clientHeight / img.naturalHeight;
    const el = document.createElement("div");
    el.className = "measure-overlay";
    el.style.left = (m.x0 * sx) + "px";
    el.style.top = (m.y0 * sy) + "px";
    el.style.width = ((m.x1 - m.x0) * sx) + "px";
    el.style.height = ((m.y1 - m.y0) * sy) + "px";
    el.title = "Measure " + m.measure;
    el.addEventListener("click", (ev) => {{
      ev.preventDefault();
      playFrom(m.onset_seconds, m.measure);
    }});
    wrap.appendChild(el);
    overlayNodes.set(m.measure, el);
  }}
}}

async function loadMidiForCurrent() {{
  const mvt = movementSel.value;
  const buf = await (await fetch(`${{MIDI_DIR}}/mvt${{mvt}}.mid`)).arrayBuffer();
  if (!seq) seq = new Sequencer(synth);
  seq.loadNewSongList([{{ binary: buf, fileName: `mvt${{mvt}}.mid` }}]);
}}

async function playFrom(seconds, measureNum) {{
  await ensureAudio();
  await ctx.resume();
  if (!seq) await loadMidiForCurrent();
  seq.playbackRate = tempoRate;
  seq.currentTime = seconds;
  seq.play();
  statusEl.textContent = `Playing from m.${{measureNum}} at ${{tempoRate.toFixed(2)}}x…`;
  startHighlightLoop();
}}

function stop() {{
  if (seq) {{ try {{ seq.pause(); seq.currentTime = 0; }} catch (e) {{}} }}
  if (rafId) cancelAnimationFrame(rafId);
  rafId = null;
  for (const el of overlayNodes.values()) el.classList.remove("playing");
}}

function startHighlightLoop() {{
  let lastMeasure = -1;
  const tick = () => {{
    if (!seq) return;
    const t = seq.currentTime;
    // Find the latest measure whose onset <= t.
    let current = -1;
    for (const m of measures) {{
      if (m.onset_seconds <= t) current = m.measure; else break;
    }}
    if (current !== lastMeasure) {{
      for (const el of overlayNodes.values()) el.classList.remove("playing");
      const el = overlayNodes.get(current);
      if (el) el.classList.add("playing");
      lastMeasure = current;
    }}
    rafId = requestAnimationFrame(tick);
  }};
  rafId = requestAnimationFrame(tick);
}}

playBtn.addEventListener("click", async () => {{
  await ensureAudio();
  await loadMidiForCurrent();
  await playFrom(0, 1);
}});
tempoSlider.addEventListener("input", () => {{
  tempoRate = parseFloat(tempoSlider.value);
  tempoLabel.textContent = tempoRate.toFixed(2) + "x";
  if (seq) seq.playbackRate = tempoRate;
}});
stopBtn.addEventListener("click", stop);
movementSel.addEventListener("change", async () => {{
  if (seq) {{ try {{ seq.pause(); }} catch (e) {{}} seq = null; }}
  await loadMovement(movementSel.value);
}});
document.addEventListener("keydown", (ev) => {{
  if (ev.key === " ") {{
    ev.preventDefault();
    if (!seq) return;
    if (seq.paused) seq.play(); else seq.pause();
  }}
  if (ev.key === "Escape") stop();
}});
window.addEventListener("resize", () => {{
  // Re-render overlays if the page scales.
  document.querySelectorAll(".measure-overlay").forEach(n => n.remove());
  overlayNodes.clear();
  renderOverlays();
}});

loadMovement(movementSel.value);
</script>
</body>
</html>
"""

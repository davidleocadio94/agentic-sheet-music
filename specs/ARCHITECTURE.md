# Architecture — agentic-sheet-music

## Pipeline

```
  ┌───────────┐   ┌─────────────┐   ┌──────────────┐   ┌──────────┐
  │  Ingest   │ → │     OMR     │ → │   Harmony    │ → │  Slides  │
  │ (pdf/img/ │   │ (→MusicXML) │   │  Analysis    │   │  (deck)  │
  │  xml/mid) │   │             │   │              │   │          │
  └───────────┘   └─────────────┘   └──────┬───────┘   └──────────┘
                                           │
                                           ▼
                                     ┌──────────┐
                                     │  Player  │
                                     │ (midi→wav│
                                     │  + clip  │
                                     │  index)  │
                                     └──────────┘
```

Each stage is a pure function with a typed input and typed output. No global state.

## Typed contracts

```python
# src/types.py (sketch)

@dataclass(frozen=True)
class Score:
    """Canonical internal score representation — built from MusicXML."""
    musicxml_path: Path
    title: str
    parts: tuple[Part, ...]
    meta: ScoreMeta
    source_confidence: float  # 0.0 (bad OMR) .. 1.0 (clean XML input)

@dataclass(frozen=True)
class HarmonyAnalysis:
    score: Score
    key_regions: tuple[KeyRegion, ...]        # possibly multiple keys over time
    chords: tuple[ChordEvent, ...]            # one per analytical beat
    cadences: tuple[Cadence, ...]
    roman_numerals: tuple[RomanEvent, ...]
    ambiguities: tuple[Ambiguity, ...]        # never hide these

@dataclass(frozen=True)
class SlideDeck:
    source: HarmonyAnalysis
    slides: tuple[Slide, ...]
    output_path: Path

@dataclass(frozen=True)
class AudioRender:
    source: Score
    full_wav: Path
    section_wavs: tuple[SectionAudio, ...]    # indexed by measure range
    midi: Path
```

## Module responsibilities

Package root: `src/agentic_sheet_music/`. Typed contracts live in `types.py`.

### `omr/`
- `ingest.py` — sniff file type, dispatch to correct converter.
- `pdf_to_musicxml.py` — wrap `oemer` / `audiveris`.
- `musicxml_loader.py` — validate MusicXML → `Score`.
- `confidence.py` — score OMR output quality (notehead density, stem consistency, etc.).

### `harmony/`
- `key_detection.py` — Krumhansl-Schmuckler + windowed re-analysis for modulations.
- `chord_extraction.py` — group simultaneous pitches into chord events.
- `roman.py` — `music21` roman-numeral analysis + custom rules.
- `cadence.py` — pattern-match authentic / plagal / half / deceptive.
- `ambiguity.py` — emit alternative readings when confidence < threshold.

### `slides/`
- `deck_builder.py` — `HarmonyAnalysis` → slide tree.
- `score_snippet.py` — render a measure range as SVG via `music21`.
- `templates/` — Marp / HTML templates.

### `player/`
- `synth.py` — MusicXML → MIDI → WAV via `fluidsynth`.
- `clipper.py` — slice per-section clips keyed by measure.

## Agents vs code

A **subagent** is used when judgment is needed:
- `harmony-analyst` reads analysis output and flags musical issues a static rule engine would miss.
- `music-theory-reviewer` acts as a second opinion before a deck ships.

The **pipeline itself is deterministic code** — not a chain of LLM calls. Agents augment the code; they don't replace it.

## Failure modes & surfacing

- **OMR noise** → attach `source_confidence` to every downstream artifact. Deck shows a banner when < 0.8.
- **Key ambiguity** → `ambiguities` tuple. Deck renders both readings side-by-side.
- **Missing dependency** (fluidsynth, lilypond) → pipeline returns partial result; slide deck still renders, audio section says "audio unavailable — install fluidsynth".

## Testing strategy

- `tests/fixtures/` holds small MusicXML files for every theory case we want to pin down (I-IV-V-I, ii-V-I, deceptive cadence, modulation to relative minor, etc.).
- Unit tests per module; integration tests for full pipeline on fixtures; no network in tests.
- OMR tests use a *fixed* rendered PDF (rendered from known MusicXML) so we can assert round-trip accuracy.

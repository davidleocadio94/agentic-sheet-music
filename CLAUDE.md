# agentic-sheet-music

Reads sheet music (PDF, MusicXML, images) → extracts notes & chords → identifies harmony → renders educational slide decks → plays back audio so the user can hear what they're looking at.

## Stack (target)

- **Language:** Python 3.11+
- **OMR (Optical Music Recognition):** `oemer` (primary) with fallback to `audiveris` CLI for PDFs; MusicXML parsing via `music21`.
- **Music theory / harmony:** `music21` for roman-numeral / functional analysis, plus custom rules in `src/harmony/`.
- **Slides:** Marp or `python-pptx` for decks; SVG snippets rendered with `music21` + `Lilypond`/`Verovio`.
- **Audio playback:** `music21` → MIDI → `fluidsynth` (SoundFont) for WAV/preview, or raw MIDI for browser playback.
- **Tests:** `pytest` with fixtures under `tests/fixtures/` containing small MusicXML and score images.

Directory layout:

```
src/agentic_sheet_music/
  omr/        # image/PDF → MusicXML
  harmony/    # MusicXML → chord symbols + roman numerals + key analysis
  slides/     # harmony analysis → slide deck
  player/     # MusicXML/MIDI → audio
  types.py    # frozen-dataclass contracts between stages
  cli.py      # `analyze` entrypoint
tests/
inputs/       # user-provided scores (gitignored except samples)
outputs/      # generated decks, audio, analyses (gitignored)
specs/        # PRD + architecture
```

## Workflow

1. **Spec first.** Every non-trivial feature starts with a doc in `specs/`. See `specs/PRD.md` and `specs/ARCHITECTURE.md`.
2. **TDD.** Write a failing pytest in `tests/` before implementation. 80%+ coverage target.
3. **Immutability.** Pipeline stages return new objects; never mutate inputs. Each stage takes a typed input and returns a typed output (`Score`, `HarmonyAnalysis`, `SlideDeck`, `AudioRender`).
4. **Small files.** 200–400 lines typical, 800 max. Extract modules by musical concept (chord, key, cadence), not by layer.
5. **Validate at boundaries.** Any file the user hands us (PDF, image, XML) is untrusted — validate before parsing.

## Build & test

- `uv sync` — install deps (using `uv` + `pyproject.toml`)
- `pytest` — full suite
- `pytest tests/harmony/` — one module
- `ruff check . && ruff format .` — lint + format
- `mypy src/` — type-check

## Agents available in this project

Defined in `.claude/agents/`:

- **omr-specialist** — converts PDFs/images to MusicXML, troubleshoots OMR quality.
- **harmony-analyst** — runs roman-numeral / functional analysis on MusicXML.
- **slide-designer** — turns harmony analyses into educational decks.
- **audio-engineer** — MusicXML/MIDI → playable audio.
- **music-theory-reviewer** — verifies harmonic analysis is musically correct.

Plus the global ones (planner, code-reviewer, tdd-guide, etc.) from `~/.claude/agents/`.

## Skills

Defined in `.claude/skills/`:

- **analyze-score** — end-to-end: score file → harmony analysis + deck + audio.
- **explain-harmony** — human-readable explanation of a harmonic progression.
- **new-feature** — scaffolds a new pipeline stage with spec + tests.

## Non-negotiables

- **Musical correctness > everything.** A green test suite on the wrong analysis is a bug, not a win. Every real-world test (anything marked `@pytest.mark.omr_binary` or running on `milonga.pdf`) must assert **specific musical claims** (key, roman numeral, cadence location) — not just "produces some output." If the piece is in D minor, the test must fail unless the detector says D minor.
- **Never trust one signal alone.** Key detection uses KS *and* the MusicXML key signature *and* mode declaration. Roman-numeral assignment uses NCT filtering *and* secondary-dominant look-ahead *and* the chord-tone-count tie-breaker. Any stage that produces an answer from a single heuristic is a future bug.
- **Before shipping a stage, run it on the real fixture** (milonga.pdf) and **read the output with a musician's eyes.** If a Cardoso milonga in D minor is reading as B♭ major for the first six measures, the stage isn't done — it's wrong. Re-open the spec and fix the root cause. Do not advance to the next stage with known-wrong output from the previous one.
- Never silently fall back to a degraded analysis. If OMR confidence is low, surface it to the user. If key detection contradicts the MusicXML key signature, surface that as an ambiguity, not a silent override.
- Never overwrite source PDFs or user-provided inputs. Derivatives go to `outputs/<piece>/` with timestamped filenames.
- No hardcoded SoundFont paths or absolute user paths in source; use `config.py` + env vars.

@specs/PRD.md
@specs/ARCHITECTURE.md

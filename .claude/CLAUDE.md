# Project: agentic-sheet-music

## Mission
PDF in → annotated PDF + per-measure click-to-play playback server out.
The pipeline is: Gemini Vision (the only OMR engine) → MusicXML → harmony
analysis + annotated PDF + MIDI playback.

## How "done" is defined

```
   GT MusicXML  ──Verovio──▶  source.pdf  ──Gemini──▶  candidate.musicxml
        │                                                     │
        └────── per-measure exact-match evaluator ◀───────────┘
                              │
                              ▼
                          score ∈ [0, 1]
```

**Metric:** per-measure exact-match.
For each measure in GT, build a signature
`tuple(voice, beat-offset-in-quarters, pitch, duration-in-quarters)`.
A measure matches iff candidate signature == GT signature exactly.
`score = matched / total`.

**Exit condition for the autoresearch loop:** `score == 1.0` across every
fixture in `eval-fixtures/`. No partial credit. No "99.9% acceptable."

## Hard constraints

- **Repo size ≤ 2 GB** total (the loop self-aborts on violation).
- **Pythonic only.** Tools are OK if invokable from Python via subprocess
  (verovio, fluidsynth). NOT OK: anything that requires opening a GUI app
  or manual user clicks (no MuseScore "open this in the app").
- **`uv add` and `brew install` are allowed** for new tools, but every
  failed experiment must uninstall what it added (loop enforces this
  automatically).
- **Evaluator and fixtures are immutable during a loop run** —
  the metric must stay constant. New fixtures land between runs only.

## The autoresearch loop (`uv run improve-omr`)

```
forever (until score==1.0 OR --max-hours OR Ctrl-C):
  1. snapshot pyproject.toml + brew + repo size
  2. uv run eval → eval-runs/iter_NNN.json
  3. score == 1.0 → STOP
  4. spawn `claude -p` with:
       - this iter's failures
       - eval-runs/MEMORY.md (rolling digest of past attempts)
       - last 5 eval-runs/iter_*.md
       - score-history.csv
       - .claude/CLAUDE.md + .claude/rules/loop-guardrails.md
       tools: Read/Write/Edit/Bash/WebSearch/WebFetch/Agent
  5. agent picks ONE experiment, implements, runs eval, commits or reverts
  6. agent writes eval-runs/iter_NNN.md
  7. post-iter guardrails: size, deps, evaluator/fixtures untouched, tests pass
       — any failure of #7 triggers automatic revert + uninstall
  8. update score-history.csv + score-plot.png + MEMORY.md
  9. loop
```

## Memory across iterations

Each iter writes `eval-runs/iter_NNN.md`. The loop deterministically rebuilds
`eval-runs/MEMORY.md` by clustering across all iter docs:

- **❌ DOESN'T WORK** (with reasons)
- **✅ WORKS** (with score deltas)
- **💡 OPEN HYPOTHESES** (proposed but not tried)
- **⚠️ KNOWN PITFALLS** (e.g. high per-call variance)

Every spawned agent reads MEMORY.md before deciding what to try.
Don't repeat failed experiments without justification.

## Process management

```
start:   $ ./scripts/start-loop.sh
         (wraps tmux + caffeinate + uv run improve-omr)
peek:    $ tmux attach -t improve-omr
detach:  Ctrl-B then D
kill:    $ pkill improve-omr-loop
            OR
         $ kill $(cat eval-runs/loop.pid)
            OR
         $ tmux kill-session -t improve-omr
```

The loop process is named `improve-omr-loop`; agent subprocesses are named
`improve-omr-iter-N` (via setproctitle). PID is in `eval-runs/loop.pid`.

## Stack

- **Gemini 3.1 Pro Vision** — only OMR engine (PDF → MusicXML)
- **Verovio** — engraves MusicXML → SVG (used for fixture PDFs)
- **cairosvg** — SVG → PDF
- **PyMuPDF** — PDF → PNG (for Gemini input + crop logic)
- **music21** — MusicXML parsing for the evaluator
- **matplotlib** — score-vs-iteration plot
- **setproctitle** — process names for kill helpers

## Forbidden during loop iterations

- Editing `src/agentic_sheet_music/eval/evaluator.py`
- Editing `eval-fixtures/**`
- Editing `tests/eval/**`
- Removing existing dependencies the project already relies on
- GUI-only tools

## Real-world stretch goal

After 100% on synthetic fixtures, add real-PDF fixtures (`07-real-pieces/`)
with hand-built ground truth via the `ground-truth-builder` agent, and push
to 100% on those too. Only THEN do we restore the annotated PDF + playalong
server features that were stubbed during the burn-down.

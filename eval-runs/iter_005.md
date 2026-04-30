# Iteration 005 — reverted

## Score: 55.6% → (no measurement) (Δ = N/A — eval did not complete)

## Experiment
  category: prompt
  description: Add a MANDATORY per-measure XML-comment "vision trace" to the
  Gemini prompt. New Step 7 in `_PAGE_PROMPT` instructed the model to emit,
  as the first child of every `<measure>`, a `<!-- trace: 1=L1(E4) 2=L2(G4)
  ... -->` listing each notehead's literal staff position (`L1..L5` lines,
  `S1..S4` spaces, `LB1..3`/`SB1..3`/`LA1..3`/`SA1..3` for ledger
  lines/spaces below/above) paired with the derived letter+octave. Defined
  the position vocabulary for treble + bass clefs, gave three worked-example
  traces (C-major arpeggio all-on-lines, C-major scale alternating,
  D-major arpeggio all-on-spaces), and added a self-consistency check
  ("if your trace says `1=L1(E4) 2=L2(F4)` that is INTERNALLY INCONSISTENT
  — fix it"). Goal: force the model to commit to a staff position before
  committing to a letter, attacking the line-vs-space confusion that
  failed all three iter-2 fixtures (02-rhythm/01, 03-meter/01, 04-key/01)
  by reading arpeggios as scales.
  files changed: src/agentic_sheet_music/omr/gemini_omr.py (added a Step 7
  block at the end of `_PAGE_PROMPT`, added ~75 lines of prompt text).

## Outcome
  REVERTED
  why: After 11 minutes only 2 of 6 fixtures had refreshed candidate.musicxml
  (01-pitch/01-c-major-scale at 06:48, 01-pitch/02-octave-leap at 06:51).
  At 06:57 fixture 02-rhythm/01-quarter-eighth had been running for 6+
  minutes with the eval process in state S (blocked on I/O — Gemini API).
  Extrapolating, the full N=5×6 = 30-call eval would have taken roughly
  35–40 minutes, blowing past the 20-minute hard timeout. Killed the
  process at 11:05 elapsed and reverted gemini_omr.py to baseline (the
  iter-2 state with autocontrast + 2× sharpening — the current best at
  55.6%). No score recorded for this iter.

## What I learned

  1. **The trace prompt did not fix the failure mode in the one fixture
     where we got to inspect output.** Fixture `01-pitch/02-octave-leap`'s
     ground truth is C4-C5-C4-C5 (octave leap, all C's). The voted
     candidate from iter 5 reads C4-F5-C4-... — still wrong, just in a
     different way. (Note: voting strips XML comments via
     `ET.tostring()`, so we cannot post-hoc inspect what trace the model
     actually wrote — the trace is purely a generation-side anchor that
     gets discarded before evaluation. We can only see whether the
     committed letters got better. They did not, in this one observable
     case.)

  2. **Token-heavy prompts blow past the iter timeout even at fixed N.**
     Adding ~75 lines of prompt + requiring the model to write trace
     comments for every measure roughly doubled per-call latency from
     ~25 seconds (iter 2) to ~60+ seconds. With N=5 self-consistency
     and 6 fixtures, that's 30 calls × ~60s ≈ 30 min of API time —
     plus stitching/voting overhead — which exceeds the 20-min hard
     ceiling. Iter 4 hit the same wall via image size; iter 5 hit it
     via prompt size and required-output size. **The lesson: any
     experiment whose effect is to make the model produce more output
     per call must drop N to compensate** (drop to N=3 or N=1) before
     launching, or at minimum time a single call first.

  3. **MEMORY.md's "20-minute timeout post-mortem" advice from iter 4 is
     real and bites again.** Same operational failure mode as iter 4
     even though the experiment category was different. Future agents:
     before launching an eval with any prompt or image change that
     plausibly increases per-call time, do `time DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run eval --notes ... --json /tmp/test.json` against
     **just one fixture** (e.g. only `01-pitch/01-c-major-scale`) first,
     measure single-call latency, and only commit to the full eval if
     6× that latency × N < 18 min. There is no "I'll just see how it
     goes" — the loop kills you at 20 min and you produce no signal.

  4. **The trace idea itself may still be sound — but needs to be
     time-budget-aware.** Two specific salvageable variants for the
     next agent:
     - Run with N=1 (single sample, no voting) so 6 fixtures × 60s =
       6 minutes. Lose the variance-smoothing benefit but gain a real
       measurement of whether the trace anchor helps.
     - Or: instead of REQUIRING the trace inside MusicXML, ask Gemini
       to first emit the trace as a JSON sidecar via response_schema
       (multi-part response), then parse the sidecar separately. Avoids
       the comment-stripping issue and may run faster because JSON is
       more compact than `<note>` blocks.

## Hypothesis for next iteration

  Two reasonable next steps, in priority order:

  A) **Simpler timing test of the iter-5 idea.** Drop `DEFAULT_NUM_SAMPLES`
     from 5 to 1 in `gemini_omr.py` (a single-line change), keep the iter-5
     vision-trace prompt addition, and run the eval. If it converges in
     <12 min and the score is ≥ 55.6%, the trace idea works and we can
     re-add voting later. If the score is <55.6%, the trace idea is
     dead — and we know it fast. **This is the cheapest informative
     experiment.**

  B) **Different attack vector: response_schema.** The current code uses
     no `response_schema` — Gemini just emits text. Switch to a structured
     schema that constrains the output to: per-measure `clef`, `key`,
     `time`, `divisions`, then per-note `staff_position` (enum L1..L5,
     S1..S4, LB1..LB3, SB1..SB3, LA1..LA3, SA1..SA3) + `octave_modifier`
     + `accidental` + `duration_beats` + `voice`. Then post-process the
     JSON into MusicXML on the Python side, using a *deterministic*
     mapping from staff_position → step+octave. This moves the
     line-vs-space distinction from "thing the model has to remember to
     follow rules about" to "thing the model literally outputs" — and
     the hard rule (L1 = E4, no exceptions) lives in Python, not in
     prompt english. Bigger refactor (~100 lines) but attacks the root
     cause structurally.

## Cleanup
  deps installed and removed: none
  files added and deleted: none. The only file that was modified
  (`src/agentic_sheet_music/omr/gemini_omr.py`) was reverted via
  `git checkout --`. Two candidate.musicxml files in eval-fixtures/
  were overwritten by the partial run; they are build artifacts (not
  tracked by git) and will be regenerated by the next iter's eval.

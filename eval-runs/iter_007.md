# Iteration 007 — reverted

## Score: 55.6% → 37.5% (Δ = -18.1)

## Experiment
  category: chunk
  description: Multi-image input. In addition to the original cropped page
  PNG, send Gemini a SECOND image of the SAME page upscaled 2× via PIL
  bicubic resampling. The two image parts are passed in `contents=[orig,
  zoomed, prompt]`. The prompt was prefaced with a paragraph telling the
  model the two images contain identical music — the upscale exists to
  disambiguate vertical position (line vs. space, octave) when noteheads
  look ambiguous in the first image. Hypothesis: doubling the pixel-per-
  notehead density in a second image gives the model more visual evidence
  to commit to a staff position without requiring additional prompt text
  or annotation overhead. files changed:
  src/agentic_sheet_music/omr/gemini_omr.py (added `_upscale_png` helper
  ~14 lines; threaded a second `Part.from_bytes` through the API call;
  prefaced `_PAGE_PROMPT` with a 5-line "you are given TWO images of the
  SAME page" paragraph). Pre-flight single-call latency on
  01-pitch/01-c-major-scale was 28.3 s — comfortably below the 6 fixtures
  × 3 samples × 28 s ≈ 8.4 min budget.

## Outcome
  REVERTED
  why: Eval finished in ~12 min but the score dropped from 55.6% to 37.5%
  (3 of 8 measures matched, 1 of 6 fixtures passed). Net regressions:
   - 03-meter/01-3-4-time: 1.0 → 0.0 (lost both measures it had been
     getting right at baseline).
   - 02-rhythm/02-dotted-quarter: dropped to 0/0 with a Gemini 504
     DEADLINE_EXCEEDED error — the larger payload (orig + 2× zoom) tipped
     this fixture's call into the API server-side timeout.
  No fixture improved over baseline. The one-pass smoke test on
  01-pitch/01-c-major-scale (where the timing was measured) read the
  scale correctly, but the same fixture always reads correctly at
  baseline too — it's the easiest in the set and the timing test
  proved nothing about the harder fixtures.

## What I learned
  1. **Multi-image with the same content at two scales does NOT help
     this model on this task.** The upscaled second image is plausible
     in principle (more pixels per notehead → better localisation), but
     it produced WORSE answers than the single original image. Several
     possible reasons:
       - The 2× upscaled image lacks anti-aliasing detail — bicubic
         doesn't add information, just enlarges existing pixels. The
         model gains no real visual evidence; it just sees blockier
         renders of the same notes. If the model attends to the upscale
         preferentially because it's larger, it may actually get worse
         input than the sharpened original.
       - "Two images of the same content" may be confusing the model
         into believing it should transcribe TWO pages, then merging
         the partial outputs unevenly. The 03-meter fixture went from
         2/2 to 0/2, suggesting the model lost track of where one
         staff/measure ends and the next begins.
       - The 504 on 02-rhythm/02-dotted-quarter shows the larger
         payload hits an API server-side limit even when our client-
         side 120 s timeout is generous. Adding more image bytes is
         not free latency-wise even if pre-flight timing on one
         fixture says it is.

  2. **Pre-flight timing on the EASIEST fixture is not predictive.**
     01-pitch/01-c-major-scale finished in 28.3 s; later fixtures took
     longer per call and one timed out at the API. Future agents who
     do pre-flight timing should use the HARDEST fixture (whichever
     has the most measures or the densest score), not the simplest.

  3. **The `chunk` category is now off the obvious-wins list.** Sending
     two images of the same content didn't help. A genuinely different
     chunking — e.g. one image PER MEASURE, individually cropped, with
     transcription per crop — has not been tried and is qualitatively
     different (Gemini sees one measure at a time, no global context).
     That's the salvageable path forward in this category.

## Hypothesis for next iteration
  Three reasonable next paths, in priority order:

  A) **per-measure crop chunking.** Detect measure boundaries in the
     rendered page (vertical barlines, easy to find from column-darkness
     peaks), crop one image per measure, send each to Gemini separately
     with the same prompt, then concatenate the per-measure outputs.
     Each call is small → latency stays low even at N=3. The model has
     a much narrower task per call: 4–8 noteheads instead of 30+. This
     is the only chunking variant that has not been tried and that
     plausibly fixes the line/space confusion (each measure is a small
     image with no spatial competition for the model's attention).
     Estimated complexity: ~80 lines (barline detection +
     per-measure stitching). Risk: barline detection has to be
     reliable, but verovio-rendered staves are clean.

  B) **schema-constrained output.** The iter-5 doc proposed switching
     to Gemini's `response_schema` with an enum for staff position
     (L1..L5, S1..S4, ledger-above/below). That moves the line/space
     decision from "things the model has to remember to follow rules
     about" to "thing the model literally outputs as a closed-set
     enum", and we map enum→step+octave deterministically in Python.
     Bigger refactor (~120 lines) but attacks the failure mode at the
     model interface, not just at the prompt level.

  C) **prompt simplification.** The current `_PAGE_PROMPT` is ~200
     lines and has accumulated layers of hand-written failure-mode
     warnings (steps-vs-thirds, octave errors, top-of-staff confusion).
     There is no evidence these long warnings help — every iteration
     since they were added has had the same kinds of errors. A
     controlled experiment: replace the prompt with a 30-line minimal
     version (just the schema + clef/key/time instructions, no
     "this is a common error" sections), run the eval, see if score
     changes. If score is similar or higher, the long prompt is not
     pulling its weight and we should keep it short. If score drops,
     the long prompt IS helping and we should focus optimisation
     elsewhere.

  Path A is highest expected-value: it changes the model's task in a
  structurally favorable way without adding prompt cruft, and the
  failure modes (line/space confusion in a 6-note measure) get
  visually trivial when the measure is the only thing in the image.

## Cleanup
  deps installed and removed: none
  files added and deleted: none. The only file modified
  (`src/agentic_sheet_music/omr/gemini_omr.py`) was reverted via
  `git checkout --`. eval-fixtures/*/*/candidate.musicxml were
  overwritten with iter-7 outputs but those are build artifacts (not
  tracked by git) and will be regenerated by the next iter's eval.

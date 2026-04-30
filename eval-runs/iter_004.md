# Iteration 004 — reverted

## Score: 55.6% → (no measurement) (Δ = N/A — eval did not complete)

## Experiment
  category: preproc
  description: Add a per-note diatonic-position label overlay to each rendered
  page PNG before sending to Gemini. Detect horizontal staff lines from row
  darkness in the rendered image, group every 5 evenly-spaced peaks into a
  staff, then draw red labels at line positions (E4 G4 B4 D5 F5) and blue
  labels at space positions (F4 A4 C5 E5) plus 3 ledger positions above and
  3 below (B3 C4 D4 G5 A5 B5). Labels appear at BOTH the left and right
  margins (added 240px white padding to each side), each with a thin colored
  tick mark aligned pixel-accurate with the actual staff line/space row in
  the source image. Updated the prompt to instruct the model: "trace
  horizontally from each notehead to either margin; the colored tick at the
  same horizontal level as the notehead's center gives the diatonic letter
  and octave; then apply the key signature for sharps/flats."
  files changed: src/agentic_sheet_music/omr/gemini_omr.py (new
    `_detect_staff_lines`, `_group_staves`, `_annotate_treble_staff_positions`
    helpers; `_enhance_png` now calls the annotator; new
    READ-THE-MARGIN-LABELS section at the top of `_PAGE_PROMPT`).

## Outcome
  REVERTED
  why: The eval did not complete within the 20-minute hard timeout. After
  41 minutes only ONE fixture (`01-pitch/01-c-major-scale`, the easiest one)
  had a refreshed `candidate.musicxml`. The other 5 fixtures were still in
  flight. The Gemini API requests were either being throttled or stalling
  on the larger annotated images (image is now ~206 KB up from ~180 KB; the
  PNG width grew from ~8234 px to ~8714 px due to the 240 px label margins
  on each side). With N=5 self-consistency at the default, this is 30 calls
  total, and at the observed rate of 1 fixture / 41 min it would have taken
  ~4 hours to finish. Reverted code to baseline; no score recorded for this
  iter.

## What I learned
  Standalone smoke-testing the preproc on a single fixture image (visual
  spot check via Read on the annotated PNG) showed the labels are
  pixel-aligned with staff lines and look readable to a human eye. The
  experiment design is plausible — the failure here was operational, not
  conceptual.

  Two specific takeaways for next agents who try a similar overlay:
  1. **Never commit to N=5 + per-page preprocessing growth without first
     measuring single-call latency under the new image size.** A 4× larger
     vertical pad + extra LR padding pushed the image past whatever
     internal Gemini threshold makes the call slow. Run ONE call against
     ONE fixture first and time it, before launching the full 30-call eval.
  2. The image height before annotation was 1133 px (cropped). After
     annotation we keep that height but add 480 px of horizontal pad —
     image grew from ~8234×1133 to ~8714×1133. That's a small relative
     change in pixel count; the slowdown was probably either Gemini-side
     queuing (API was slow tonight in general) or genuinely slower
     processing of the labeled image. We can't distinguish without a
     proper timing run.

  The staff-line detector itself works reliably on the test fixture. Code
  for it is in git history of this revert and worth resurrecting next iter.

## Hypothesis for next iteration
  Same overlay idea, but TIME-BOUNDED:
  1. Drop N from 5 to 1 (or 2) during the experimental run so the eval
     completes within the iter timeout. We can re-add multi-sampling later.
  2. Before running the full eval, time a single Gemini call on a single
     fixture page with the annotated image — if it takes >2× the baseline,
     simplify the overlay (e.g. labels only at one margin, or skip ledger
     positions, or use a smaller font).
  3. Alternatively: instead of overlay, append a SECOND rendered image to
     the request — original page first, then a "lookup card" image of just
     the labeled blank staff (no music). Two separate images, smaller each.
     Gemini is documented as handling multi-image inputs. The model can
     visually align them by the staff geometry. This avoids modifying the
     music image itself.

  Either approach attacks the same root cause: line/space confusion. The
  iter-003 voted output still shows D-E-F#-G (scale) where GT has D-F#-A-D
  (arpeggio); the current verbose prompt-only intervention is not enough.

## Cleanup
  deps installed and removed: none
  files added and deleted: none (revert restored gemini_omr.py to baseline;
  /tmp/keytest*.png are local scratch only)

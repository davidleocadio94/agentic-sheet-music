---
name: analyze-score
description: End-to-end pipeline for a score file — OMR (if needed) → harmony analysis → educational slide deck → audio render. Use when the user gives you a sheet music file (PDF, image, MusicXML, MIDI) and wants the full treatment.
argument-hint: "<path-to-score>"
allowed-tools: Read, Glob, Bash(uv run *), Bash(python *), Bash(ls *)
---

# Analyze score: $ARGUMENTS

Run the full analysis pipeline on `$ARGUMENTS` and open the results.

## Steps

1. **Validate input.** Check the file exists and is a supported type (`.pdf`, `.png`, `.jpg`, `.musicxml`, `.xml`, `.mid`, `.midi`). If not, stop and explain.
2. **Ingest + OMR if needed.**
   - If MusicXML/MIDI: skip to step 3.
   - If PDF/image: delegate to the `omr-specialist` agent. If confidence is low (<0.7), surface that to the user and ask whether to continue.
3. **Harmony analysis.** Delegate to `harmony-analyst`. Output: `outputs/<piece>/analysis.json`.
4. **Review.** For any piece longer than 16 measures, invoke `music-theory-reviewer` on the analysis before continuing. If verdict is `fundamentally-broken`, stop and report.
5. **Build slide deck.** Delegate to `slide-designer`. Output: `outputs/<piece>/deck.html`.
6. **Render audio.** Delegate to `audio-engineer`. Output: `outputs/<piece>/audio/`.
7. **Report.** Summarize: key(s), number of chords labeled, cadences found, ambiguities, output paths. Do NOT auto-open the browser; print the path.

## Failure handling

- Any stage can produce a partial result. Never hide a failure behind a success message.
- If a system dep is missing (fluidsynth, lilypond), finish the stages that don't need it, then list what's missing.

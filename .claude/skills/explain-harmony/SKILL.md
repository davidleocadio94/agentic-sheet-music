---
name: explain-harmony
description: Produce a human-readable explanation of a harmonic progression or passage. Use when the user points at a specific section of an already-analyzed piece and asks "why does this work" or "what's happening here."
argument-hint: "<piece-name> <measure-range>"
allowed-tools: Read, Glob, Grep
---

# Explain harmony: $ARGUMENTS

Explain the harmony in the given measure range like a teacher, not a data dump.

## Steps

1. Locate `outputs/<piece>/analysis.json`. If missing, tell the user to run `analyze-score` first.
2. Extract the relevant measure range from the analysis.
3. Write a 3–6 sentence explanation covering, in order:
   - The local key and why we're in it.
   - The chord-to-chord motion in functional terms (T → PD → D → T, secondary dominants, etc.) — not raw roman numerals.
   - Anything unusual (mixture, chromatic mediants, deceptive moves, suspensions worth naming).
   - What the passage *sounds like* and why (tension/release, surprise, color).
4. End with one roman-numeral line for reference.

## Style

- Write for a curious intermediate student, not a musicologist.
- No jargon without a one-line gloss in parens.
- Do NOT list every chord; group by function.

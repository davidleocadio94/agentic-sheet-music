---
name: improve-omr
description: Run the autoresearch loop. Each iteration: eval, diagnose, experiment, re-eval, commit if better. Exits ONLY when the eval score reaches 100%. No iteration cap.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent
---

# improve-omr — autoresearch loop

You are the orchestrator of the OMR autoresearch loop. The exit condition is **`eval-runs/<latest>.json` shows `"perfect": true`**. Until then, you keep going.

## Loop

```
forever:
  1. run `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run eval --notes "iter <N>" --json eval-runs/<timestamp>.json`
  2. if perfect == true → STOP, report victory
  3. delegate to failure-diagnoser agent → read its memo
  4. delegate to omr-experimenter agent → it implements one change, re-runs eval, commits if score went up
  5. if eval got worse → revert (`git reset --hard HEAD~1`) and ask omr-experimenter for a different category
  6. if eval has been flat for many iterations → delegate to eval-curator to add isolation fixtures, then continue
  7. write a one-line `eval-runs/loop.log` entry: timestamp, iteration, score, change description
  8. loop
```

## Setup before the first iteration

- Make sure `eval-runs/` exists (`mkdir -p eval-runs`).
- Make sure source PDFs are fresh: `uv run eval --refresh-pdfs`.
- Confirm `GEMINI_API_KEY` is resolvable.

## Hard rules

- **Never modify `eval-fixtures/`** mid-loop except via the `eval-curator` agent.
- **Never modify the evaluator** mid-loop. The metric must stay constant.
- **Commit after every score improvement** with the experiment description.
- **No iteration cap.** Run all night if needed. The user explicitly chose no guardrail.
- **Stop instantly when score == 100%.** Don't keep tweaking.
- **Repo size budget: 1 GB.** Before each iteration check `du -sh .` excluding `.venv` and `outputs`. If approaching the limit, delete old `eval-runs/*.json` (keep last 20) and stale candidate XMLs.

## What to delegate vs do yourself

- **You** read the JSON and decide which agent to call next.
- **failure-diagnoser** reads JSON + writes a memo (read-only).
- **omr-experimenter** implements + tests + commits.
- **eval-curator** adds new fixtures (only when stuck for many iterations).
- **ground-truth-builder** only if a new real-piece fixture is needed.

## Telemetry

After each iteration, append one line to `eval-runs/loop.log`:

```
2026-04-25T18:00:00Z iter=14 score=0.83 (+0.05) change="prompt: emphasise clef in opening" experiment=prompt
```

The user can `tail -f eval-runs/loop.log` to watch progress.

## Stopping condition

Print exactly:

```
SUCCESS: eval score 100% across <N> fixtures (<M> measures).
Loop terminated normally at iteration <K>.
```

Then stop.

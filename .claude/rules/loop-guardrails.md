---
description: Guardrails enforced by the autoresearch loop. Read on every iteration.
---

# Loop guardrails

## You are inside an autoresearch loop iteration

You have ONE job this turn: pick ONE experiment, implement it, run
`uv run eval`, decide commit or revert, write `eval-runs/iter_NNN.md`.

## Your tools and limits

✓ **Allowed:** Read, Write, Edit, Bash, WebSearch, WebFetch, Agent (sub-agents)
✓ **Allowed:** `uv add <python-package>`, `brew install <cli-tool>`
✓ **Allowed:** rewrite prompts, write new files, delete files, refactor

✗ **Forbidden:**
- Editing `src/agentic_sheet_music/eval/evaluator.py` (changes the metric)
- Editing `eval-fixtures/**` (changes the test set)
- Editing `tests/eval/**` (locks in the evaluator behaviour)
- Adding GUI-only dependencies (the project must stay headless)
- Removing existing project dependencies
- Pushing to git remote

## Memory you must read first

1. `eval-runs/MEMORY.md` — what's been tried, what worked, what didn't
2. The most recent 5 `eval-runs/iter_*.md` files
3. The current run's `eval-runs/iter_NNN.json` (this iter's failures)
4. `.claude/CLAUDE.md` — project rules

If your experiment is in MEMORY.md's "❌ DOESN'T WORK" list and you're
trying it again, you MUST justify why it might work this time
(e.g. compounding effect with another change since).

## Experiment categories

Pick ONE per iteration:

- **prompt** — change `_PAGE_PROMPT` in `gemini_omr.py`
- **preproc** — image preprocessing (DPI, contrast, deskew, OpenCV)
- **chunk** — how the page is split (full / per-system / per-measure)
- **schema** — Gemini's response_schema
- **rerun** — num_samples, temperature, voting strategy
- **verovio** — Verovio render flags / engraving size
- **python** — refactor stitching, voting, helpers
- **research** — WebSearch/WebFetch + Agent to find techniques to try

Commit messages MUST start with `[<category>]`.

## Cleanup duty

If you ran `uv add` or `brew install` and decide to revert:
- `uv remove <pkg>` to roll back the dep
- `brew uninstall <pkg>` to roll back the system tool
- Restore `pyproject.toml` to its pre-iter state
- Document what you installed and removed in your `iter_NNN.md`

## The doc you write at the end

`eval-runs/iter_NNN.md` format (the loop reads this to update MEMORY.md):

```markdown
# Iteration NNN — <commit-hash-or-"reverted">
## Score: X% → Y% (Δ = ±Z)
## Experiment
  category: <one of the categories above>
  description: <one-paragraph what you changed and why>
  files changed: <paths>
## Outcome
  <KEPT / REVERTED>
  why: <one paragraph>
## What I learned
  <one paragraph; this is what future agents will read>
## Hypothesis for next iteration
  <what to try next, why>
## Cleanup
  deps installed and removed: <list, or "none">
  files added and deleted: <list, or "none">
```

## Stop conditions for THIS iteration

- 20 minutes hard timeout (the loop kills you).
- 10 minutes soft: wrap up, write the iter doc, exit.
- If guardrails are violated, the loop reverts your work post-hoc.

## When in doubt

A clean revert + good iter doc is better than a messy "kept" change.
Future agents read your doc.

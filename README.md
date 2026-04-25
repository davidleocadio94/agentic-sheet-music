# agentic-sheet-music

Sheet music in → harmony analysis + educational slide deck + audio playback out.

See [CLAUDE.md](./CLAUDE.md) for the project charter and [specs/PRD.md](./specs/PRD.md) / [specs/ARCHITECTURE.md](./specs/ARCHITECTURE.md) for the design.

## Quick start (target — not yet implemented)

```bash
uv sync
uv run analyze inputs/my-score.pdf
open outputs/my-score/deck.html
```

## Status

Scaffolding only. Source modules (`src/omr`, `src/harmony`, `src/slides`, `src/player`) are stubs. Implementation follows the spec-first / TDD flow in `CLAUDE.md`.

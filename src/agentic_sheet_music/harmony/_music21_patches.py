"""Runtime patches for music21 bugs we've hit.

Keep this file tiny and each patch clearly justified. Remove patches as upstream
music21 releases fix them; track versions so we know when a patch is obsolete.
"""

from __future__ import annotations

import music21


def _patch_pitched_timespan_hashable() -> None:
    """Make `PitchedTimespan` identity-hashable so `ChordReducer` works on arpeggios.

    music21 9.9.1 sets `__hash__ = None` on `PitchedTimespan` (because it defines
    `__eq__`), but `analysis.reduceChords.ChordReducer.fillMeasureGaps` puts
    PitchedTimespan instances into sets. That set usage only needs object
    identity, not value equality — so identity hashing is correct and safe here.

    Trigger: any score with adjacent timespans in the same measure that share
    pitches (typical of arpeggios and broken-chord guitar figuration).

    Remove this patch once music21 fixes the set usage upstream (or makes the
    class value-hashable).
    """
    from music21.tree import spans

    if spans.PitchedTimespan.__hash__ is None:
        spans.PitchedTimespan.__hash__ = object.__hash__  # type: ignore[method-assign]


def apply_all() -> None:
    _patch_pitched_timespan_hashable()


_APPLIED = False


def ensure_applied() -> None:
    """Apply patches exactly once per process. Safe to call repeatedly."""
    global _APPLIED
    if _APPLIED:
        return
    apply_all()
    _APPLIED = True


__all__ = ["apply_all", "ensure_applied"]

# music21 version this file was written against; update deliberately.
_TARGET_VERSION = "9.9.1"
_ACTUAL_VERSION = music21.__version__

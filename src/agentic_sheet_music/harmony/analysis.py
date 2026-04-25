"""Bundle stage outputs into a HarmonyAnalysis."""

from __future__ import annotations

from agentic_sheet_music.types import (
    Ambiguity,
    Cadence,
    ChordEvent,
    HarmonyAnalysis,
    KeyRegion,
    RomanEvent,
    Score,
)


def build_analysis(
    *,
    score: Score,
    key_regions: tuple[KeyRegion, ...],
    chords: tuple[ChordEvent, ...],
    roman_numerals: tuple[RomanEvent, ...],
    cadences: tuple[Cadence, ...],
    ambiguities: tuple[Ambiguity, ...],
) -> HarmonyAnalysis:
    return HarmonyAnalysis(
        score=score,
        key_regions=key_regions,
        chords=chords,
        roman_numerals=roman_numerals,
        cadences=cadences,
        ambiguities=ambiguities,
    )

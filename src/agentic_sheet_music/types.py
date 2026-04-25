"""Typed contracts passed between pipeline stages.

All objects are frozen dataclasses — stages return new values, never mutate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScoreMeta:
    title: str
    composer: str | None
    time_signature: str | None
    key_signature: str | None


@dataclass(frozen=True)
class Part:
    name: str
    instrument: str | None


@dataclass(frozen=True)
class Score:
    """Canonical internal score, built from MusicXML."""

    musicxml_path: Path
    meta: ScoreMeta
    parts: tuple[Part, ...]
    source_confidence: float  # 0.0 (bad OMR) .. 1.0 (clean XML input)


@dataclass(frozen=True)
class KeyRegion:
    start_measure: int
    end_measure: int
    key: str  # e.g. "C major", "a minor"
    confidence: float | None


@dataclass(frozen=True)
class ChordEvent:
    measure: int
    beat: float
    pitches: tuple[str, ...]  # e.g. ("C4", "E4", "G4")
    label: str  # e.g. "Cmaj", "G7"


@dataclass(frozen=True)
class RomanEvent:
    measure: int
    beat: float
    numeral: str  # e.g. "V7/ii"
    key: str
    rationale: str


@dataclass(frozen=True)
class Cadence:
    kind: str  # "PAC" | "IAC" | "HC" | "DC" | "PC"
    start_measure: int
    end_measure: int
    rationale: str


@dataclass(frozen=True)
class Ambiguity:
    measure: int
    beat: float
    readings: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class HarmonyAnalysis:
    score: Score
    key_regions: tuple[KeyRegion, ...]
    chords: tuple[ChordEvent, ...]
    roman_numerals: tuple[RomanEvent, ...]
    cadences: tuple[Cadence, ...]
    ambiguities: tuple[Ambiguity, ...]


@dataclass(frozen=True)
class Slide:
    title: str
    body_markdown: str
    score_snippet_svg: str | None
    audio_clip_path: Path | None


@dataclass(frozen=True)
class SlideDeck:
    source: HarmonyAnalysis
    slides: tuple[Slide, ...]
    output_path: Path


@dataclass(frozen=True)
class SectionAudio:
    start_measure: int
    end_measure: int
    wav_path: Path


@dataclass(frozen=True)
class AudioRender:
    score: Score
    full_wav: Path | None  # None if fluidsynth unavailable
    midi: Path
    section_wavs: tuple[SectionAudio, ...]


@dataclass(frozen=True)
class MeasureDisagreement:
    measure: int
    issue: str
    suggested_fix: str | None
    confidence: float


@dataclass(frozen=True)
class PageVerification:
    page_index: int
    overall_confidence: float
    observed_key_signature: str | None
    observed_time_signature: str | None
    disagreements: tuple[MeasureDisagreement, ...]


@dataclass(frozen=True)
class ScoreVerification:
    pages: tuple[PageVerification, ...]
    model: str
    total_disagreements: int


@dataclass(frozen=True)
class AppliedFix:
    measure: int
    op: str
    description: str
    before: str
    after: str


@dataclass(frozen=True)
class SkippedFix:
    measure: int
    op: str
    reason: str
    confidence: float


@dataclass(frozen=True)
class AutoCorrectionResult:
    original_xml: Path
    corrected_xml: Path | None
    applied_fixes: tuple[AppliedFix, ...]
    skipped_fixes: tuple[SkippedFix, ...]
    verification: ScoreVerification

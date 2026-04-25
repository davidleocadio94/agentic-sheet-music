"""Chord extraction by measure-window pitch bucketing.

See specs/feature-harmony-chord-extraction.md.

Design note:
  We intentionally do NOT rely on music21.analysis.reduceChords.ChordReducer.
  That class's "arpeggio collapsing" only merges runs of identical pitches; it
  does not reduce a broken-chord figuration (F-A-C-A) to a single F-major
  harmony. For the guitar milonga use case that's a fatal gap, so instead we:

  1. chordify() the score to get a sonority per unique onset.
  2. Bucket sonorities by (measure, harmonic-rhythm window) where the window
     is measure_length / max_chords_per_measure.
  3. Collect the union of pitches in each bucket, build a music21 Chord from
     the set, and emit a ChordEvent with a quality-guess label.

  This is the pattern used by DCMLab's ms3 corpus pipeline; it's simple,
  deterministic, and doesn't depend on buggy music21 internals.
"""

from __future__ import annotations

import re
from collections import defaultdict
from fractions import Fraction
from typing import Iterable

from music21 import chord as m21chord
from music21 import converter, pitch as m21pitch

from agentic_sheet_music.harmony._music21_patches import ensure_applied
from agentic_sheet_music.types import ChordEvent, Score

ensure_applied()


class ChordExtractionError(Exception):
    """Raised when a score has no notes to reduce."""


def extract_chords(
    score: Score,
    max_chords_per_measure: int = 2,
) -> tuple[ChordEvent, ...]:
    """Reduce arpeggiated / broken-chord textures into harmonic events.

    Returns one ChordEvent per harmonic-rhythm window, in score order.
    Measures where every window is empty (all rests) produce no events.
    """
    if max_chords_per_measure <= 0:
        raise ValueError(f"max_chords_per_measure must be >= 1, got {max_chords_per_measure}")

    stream = converter.parse(str(score.musicxml_path))
    if not list(stream.flatten().notes):
        raise ChordExtractionError(f"{score.musicxml_path}: no notes to extract chords from")

    # chordify() flattens all voices/parts into a single timeline of vertical
    # sonorities. Each resulting Chord covers a time slice until the next onset.
    chordified = stream.chordify()

    events: list[ChordEvent] = []
    for measure in chordified.getElementsByClass("Measure"):
        events.extend(_extract_from_measure(measure, max_chords_per_measure))
    return tuple(events)


def _extract_from_measure(
    measure,  # music21.stream.Measure
    max_chords_per_measure: int,
) -> Iterable[ChordEvent]:
    """Bucket a measure's sonorities into windows and emit one event per non-empty window."""
    measure_number = int(measure.number) if measure.number is not None else 0
    measure_length = Fraction(measure.barDuration.quarterLength).limit_denominator(64)
    if measure_length <= 0:
        return

    window_length = measure_length / max_chords_per_measure

    # Collect (local_offset, duration, pitches) for every non-rest sonority.
    sonorities: list[tuple[Fraction, Fraction, tuple[m21pitch.Pitch, ...]]] = []
    for el in measure.recurse().notes:  # Chord | Note
        offset = Fraction(el.offset).limit_denominator(64)
        dur = Fraction(el.duration.quarterLength).limit_denominator(64)
        if isinstance(el, m21chord.Chord):
            pitches = tuple(el.pitches)
        else:
            pitches = (el.pitch,) if hasattr(el, "pitch") else ()
        if pitches:
            sonorities.append((offset, dur, pitches))

    if not sonorities:
        return

    # Bucket by window index within the measure.
    buckets: dict[int, list[tuple[Fraction, tuple[m21pitch.Pitch, ...]]]] = defaultdict(list)
    for offset, dur, pitches in sonorities:
        idx = int(offset // window_length) if window_length > 0 else 0
        if idx >= max_chords_per_measure:
            idx = max_chords_per_measure - 1  # clamp rounding at measure end
        buckets[idx].append((dur, pitches))

    for idx in sorted(buckets):
        items = buckets[idx]
        pitch_set: dict[str, m21pitch.Pitch] = {}
        weights: dict[str, Fraction] = defaultdict(lambda: Fraction(0))
        for dur, pitches in items:
            for p in pitches:
                key = p.name  # pitch-class, e.g. "B-", "F#"
                weights[key] += dur
                # Keep the lowest-octave occurrence for bass-inference.
                prev = pitch_set.get(key)
                if prev is None or p.ps < prev.ps:
                    pitch_set[key] = p

        if not pitch_set:
            continue

        # Build a music21 Chord from the unique pitches, ordered by pitch.
        ordered = sorted(pitch_set.values(), key=lambda p: p.ps)
        chord_obj = m21chord.Chord(ordered)

        window_start = idx * window_length
        beat = float(window_start) + 1.0  # music21 uses 1-indexed beats

        events_for_window = ChordEvent(
            measure=measure_number,
            beat=beat,
            pitches=tuple(p.nameWithOctave for p in ordered),
            label=_label(chord_obj, weights),
        )
        yield events_for_window


_COMMON_NAME_RE = re.compile(
    r"^(?P<root>[A-G](?:#|b)?)-(?P<quality>major triad|minor triad|diminished triad|augmented triad"
    r"|dominant seventh chord|major seventh chord|minor seventh chord"
    r"|half-diminished seventh chord|diminished seventh chord|minor-major seventh chord)$"
)

_QUALITY_SUFFIX = {
    "major triad": "",
    "minor triad": "m",
    "diminished triad": "dim",
    "augmented triad": "aug",
    "dominant seventh chord": "7",
    "major seventh chord": "maj7",
    "minor seventh chord": "m7",
    "half-diminished seventh chord": "m7b5",
    "diminished seventh chord": "dim7",
    "minor-major seventh chord": "mMaj7",
}


def _label(c: m21chord.Chord, weights: dict[str, Fraction]) -> str:
    """Guess a short chord-quality label from a music21 Chord.

    Falls back to '?' when the pitch set isn't a recognizable triad or seventh.
    The `weights` map (pitch-name -> total duration) is used for future
    weight-aware disambiguation (passing-tone filtering); currently unused.
    """
    del weights  # reserved for NCT-aware labeling in a later spec
    common = c.pitchedCommonName
    m = _COMMON_NAME_RE.match(common)
    if not m:
        return "?"
    return f"{m.group('root')}{_QUALITY_SUFFIX[m.group('quality')]}"

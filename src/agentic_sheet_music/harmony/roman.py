"""Roman-numeral assignment. See specs/feature-harmony-roman.md.

Design:
  For each ChordEvent we pick the best diatonic (or secondary-dominant) triad
  or 7th that matches the event's pitch set, using key context to filter out
  non-chord tones. Then music21.roman.romanNumeralFromChord produces the
  figure from the filtered chord tones.

  Secondary-dominant detection uses a look-ahead pass: an A7 in C major that
  resolves to Dm becomes V7/ii; one that doesn't resolve stays V7 (or becomes
  an ambiguity if both readings are defensible).
"""

from __future__ import annotations

from dataclasses import dataclass

from music21 import chord as m21chord, interval, key as m21key, pitch as m21pitch, roman

from agentic_sheet_music.harmony._music21_patches import ensure_applied
from agentic_sheet_music.types import Ambiguity, ChordEvent, KeyRegion, RomanEvent, Score

ensure_applied()


class RomanAnalysisError(Exception):
    pass


# How many triads into the key's diatonic set — 7 degrees.
_DEGREES = range(1, 8)


@dataclass(frozen=True)
class _Candidate:
    figure: str  # simplified figure like "I", "V7", "V7/ii"
    chord_tone_pitches: tuple[str, ...]  # pitches we treated as chord tones
    extras: int  # non-chord tones in the event
    chord_tone_hits: int  # chord tones present in the event
    rationale: str


def assign_roman(
    chords: tuple[ChordEvent, ...],
    key_regions: tuple[KeyRegion, ...],
    *,
    score: Score | None = None,  # noqa: ARG001 — reserved for weight-aware NCT filtering
) -> tuple[tuple[RomanEvent, ...], tuple[Ambiguity, ...]]:
    if not key_regions:
        raise ValueError("harmony-roman requires at least one key region")

    events: list[RomanEvent] = []
    ambiguities: list[Ambiguity] = []

    for i, ch in enumerate(chords):
        key_str = _key_for_measure(key_regions, ch.measure)
        key_obj = _to_music21_key(key_str)

        candidates = _candidate_labels(ch.pitches, key_obj)
        if not candidates:
            events.append(
                RomanEvent(
                    measure=ch.measure,
                    beat=ch.beat,
                    numeral="?",
                    key=key_str,
                    rationale="no diatonic chord-tone match",
                )
            )
            continue

        best = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None

        # Secondary-dominant look-ahead: if the chord looks like a dominant-7
        # or fully/half-diminished, and the next chord's root matches the
        # target-of-secondary, relabel as V7/X or viio/X.
        next_chord = chords[i + 1] if i + 1 < len(chords) else None
        sec = _check_secondary_dominant(ch.pitches, next_chord, key_obj)
        if sec is not None:
            figure, target_name = sec
            best = _Candidate(
                figure=figure,
                chord_tone_pitches=best.chord_tone_pitches,
                extras=best.extras,
                chord_tone_hits=best.chord_tone_hits,
                rationale=f"dominant of {target_name}; resolves in m.{next_chord.measure}",
            )

        events.append(
            RomanEvent(
                measure=ch.measure,
                beat=ch.beat,
                numeral=best.figure,
                key=key_str,
                rationale=best.rationale,
            )
        )

        # If two candidates tied (<= 1 chord-tone-hit apart AND same extras),
        # record an ambiguity.
        if runner_up and runner_up.chord_tone_hits >= best.chord_tone_hits and runner_up.figure != best.figure:
            ambiguities.append(
                Ambiguity(
                    measure=ch.measure,
                    beat=ch.beat,
                    readings=(best.figure, runner_up.figure),
                    rationale=(
                        f"tie between {best.figure} ({best.rationale}) and "
                        f"{runner_up.figure} ({runner_up.rationale})"
                    ),
                )
            )

    return tuple(events), tuple(ambiguities)


# ---------------------------------------------------------------------------
# helpers


def _key_for_measure(regions: tuple[KeyRegion, ...], measure: int) -> str:
    for r in regions:
        if r.start_measure <= measure <= r.end_measure:
            return r.key
    # Fallback: nearest region by measure.
    nearest = min(regions, key=lambda r: min(abs(measure - r.start_measure), abs(measure - r.end_measure)))
    return nearest.key


def _to_music21_key(key_str: str) -> m21key.Key:
    """Parse 'C major' / 'a minor' / 'Bb major' into a music21.key.Key."""
    parts = key_str.strip().split()
    if len(parts) != 2:
        raise RomanAnalysisError(f"unparseable key string: {key_str!r}")
    tonic_str, mode = parts
    mode = mode.lower()
    if mode == "minor":
        tonic_str = tonic_str.lower()
    # music21 accepts "C", "c", "Bb", "bb", "C#", "c#" etc.
    return m21key.Key(tonic_str)


def _candidate_labels(pitches: tuple[str, ...], key_obj: m21key.Key) -> list[_Candidate]:
    """For each diatonic triad/7 in the key, score how well it explains the pitch set."""
    pitch_classes = {m21pitch.Pitch(p).name for p in pitches}
    scale_pitches = key_obj.getScale().getPitches()  # one octave

    candidates: list[_Candidate] = []
    for degree in _DEGREES:
        # Build the triad for this scale degree in this key.
        for quality_figures in _figures_for_degree(degree, key_obj):
            try:
                rn = roman.RomanNumeral(quality_figures, key_obj)
            except Exception:  # noqa: BLE001 — music21 raises various
                continue
            chord_pcs = {p.name for p in rn.pitches}
            hits = len(chord_pcs & pitch_classes)
            extras = len(pitch_classes - chord_pcs)
            missing = len(chord_pcs - pitch_classes)
            if hits < 2:
                continue
            # Score: prefer more hits, fewer extras+missing.
            candidates.append(
                _Candidate(
                    figure=_simplify_figure(rn),
                    chord_tone_pitches=tuple(sorted(chord_pcs)),
                    extras=extras,
                    chord_tone_hits=hits,
                    rationale=_rationale(rn, pitch_classes, chord_pcs),
                )
            )

    # Rank: most hits wins; ties broken by fewest extras.
    candidates.sort(key=lambda c: (-c.chord_tone_hits, c.extras))
    return _dedupe_by_figure(candidates)


def _figures_for_degree(degree: int, key_obj: m21key.Key) -> list[str]:
    """Roman-numeral figure strings to try for a given scale degree.

    Always tries the plain triad for that degree; for V (and vii in minor)
    also tries the dominant-7 / fully-diminished-7 form, which is the common
    surface shape in tonal music.
    """
    digits = {1: "i", 2: "ii", 3: "iii", 4: "iv", 5: "v", 6: "vi", 7: "vii"}
    # music21's RomanNumeral takes lowercase for minor, uppercase for major;
    # we pass lowercase-or-uppercase based on what the key says is diatonic,
    # but RomanNumeral(..., key) handles case automatically from the scale,
    # so we can pass lowercase and rely on music21 to capitalize.
    base = digits[degree]
    figures = [base, base.upper()]
    if degree == 5:
        figures += ["V7"]
    if degree == 7:
        figures += ["vii°", "viio7", "viiø7"]
    return figures


def _simplify_figure(rn: roman.RomanNumeral) -> str:
    """Map music21's figured-bass-heavy figures to cleaner forms.

    'V75#3' -> 'V7' when the chord is a plain dominant 7th in minor.
    Leave non-dominant figures untouched.
    """
    fig = rn.figure
    # Minor-key V7 shows as 'V75#3' because the raised-3 is non-diatonic.
    if fig.startswith("V") and rn.seventh is not None and rn.isDominantSeventh():
        return "V7"
    if fig.startswith("v") and rn.seventh is not None and rn.isDominantSeventh():
        return "V7"  # minor-mode V always written uppercase when functional
    return fig


def _rationale(
    rn: roman.RomanNumeral,
    observed_pcs: set[str],
    chord_pcs: set[str],
) -> str:
    extras = observed_pcs - chord_pcs
    missing = chord_pcs - observed_pcs
    parts = [f"{rn.figure} in {rn.key.name}"]
    if extras:
        parts.append(f"NCT: {','.join(sorted(extras))}")
    if missing:
        parts.append(f"missing: {','.join(sorted(missing))}")
    return "; ".join(parts)


def _dedupe_by_figure(candidates: list[_Candidate]) -> list[_Candidate]:
    """Keep only the highest-scoring candidate per figure (e.g. collapse V and V7 ties)."""
    seen: dict[str, _Candidate] = {}
    for c in candidates:
        if c.figure not in seen:
            seen[c.figure] = c
    return list(seen.values())


def _check_secondary_dominant(
    pitches: tuple[str, ...],
    next_chord: ChordEvent | None,
    key_obj: m21key.Key,
) -> tuple[str, str] | None:
    """If `pitches` form a V7 (or vii°7) of a non-tonic degree and `next_chord`
    resolves to that degree, return (figure_string, target_description).
    """
    if next_chord is None:
        return None

    chord_obj = m21chord.Chord([m21pitch.Pitch(p) for p in pitches])
    if not chord_obj.isDominantSeventh() and not chord_obj.isDiminishedSeventh():
        return None

    # The root of a dominant-7 is the 5th of its target; find the target by
    # going down a perfect 5th from the root.
    root = chord_obj.root()
    if root is None:
        return None
    down_fifth = root.transpose(interval.Interval("-P5"))
    target_pc = down_fifth.name

    # Does the next chord have this pitch class as its root?
    next_chord_obj = m21chord.Chord([m21pitch.Pitch(p) for p in next_chord.pitches])
    next_root = next_chord_obj.root()
    if next_root is None or next_root.name != target_pc:
        return None

    # Is the target a non-tonic scale degree in the current key?
    # Use pitchFromDegree rather than getScale() — getScale() on minor keys
    # returns the relative major scale, which mis-indexes degrees.
    scale_degrees: dict[str, int] = {}
    for d in range(1, 8):
        scale_degrees[key_obj.pitchFromDegree(d).name] = d
    degree = scale_degrees.get(target_pc)
    if degree is None or degree == 1:
        return None  # target isn't in the key, or is tonic (just V7)

    degree_name = {2: "ii", 3: "iii", 4: "iv", 5: "V", 6: "vi", 7: "vii"}.get(degree, str(degree))
    # Lowercase if the target triad is minor in the key.
    target_rn = roman.RomanNumeral(degree_name, key_obj)
    if target_rn.quality == "minor":
        degree_name = degree_name.lower()
    elif target_rn.quality == "diminished":
        degree_name = degree_name.lower() + "°"

    figure_prefix = "viio7" if chord_obj.isDiminishedSeventh() else "V7"
    return f"{figure_prefix}/{degree_name}", degree_name

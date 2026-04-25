"""Key detection via Krumhansl-Schmuckler constrained by the declared key signature.

See specs/feature-harmony-key-detection.md and .claude/rules/correctness.md.

Design:
  The MusicXML key signature is an explicit signal from the engraver: "this piece
  sits in a key with N sharps/flats." It narrows the possibilities to the two
  parallel keys (relative major + relative minor) compatible with that signature.
  KS then picks between those two — no open-ended search across 24 keys, which is
  what mislabels the opening of the Cardoso milonga as B-flat major.

  An outside-signature key is only allowed when its KS correlation exceeds the
  best in-signature candidate by a meaningful margin (KEY_SWITCH_MARGIN) *and*
  the evidence persists for at least `window_measures` measures. This prevents
  KS hallucinations on 2–4 note windows while still catching real modulations.
"""

from __future__ import annotations

import numpy as np
from music21 import converter

from agentic_sheet_music.types import KeyRegion, Score

DEFAULT_WINDOW = 4

# An outside-signature key is only accepted if (a) it beats the in-signature
# best by KEY_SWITCH_MARGIN in correlation, AND (b) the same outside key wins
# by that margin across at least KEY_SWITCH_MIN_WINDOWS consecutive windows.
# These thresholds are tuned so a short KS burst inside a 4-measure arpeggio
# does NOT dislodge the declared home key — KS is unreliable on such short,
# texturally-biased windows. A real modulation is sustained for many measures
# and will trivially clear both thresholds.
KEY_SWITCH_MARGIN = 0.25
KEY_SWITCH_MIN_WINDOWS = 3

# Krumhansl-Kessler profiles (major and minor).
_MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)

# All 24 candidate keys: (name, tonic_pc, is_major).
_PITCH_NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_PITCH_NAMES_FLAT = ["C", "D-", "D", "E-", "E", "F", "G-", "G", "A-", "A", "B-", "B"]


class KeyDetectionError(Exception):
    """Raised when a score has no tonal content to analyze at all."""


def detect_keys(score: Score, window_measures: int = DEFAULT_WINDOW) -> tuple[KeyRegion, ...]:
    """Return non-overlapping key regions covering every measure of the score."""
    stream = converter.parse(str(score.musicxml_path))
    flat_notes = list(stream.flatten().notes)
    if not flat_notes:
        raise KeyDetectionError(f"{score.musicxml_path}: no notes to analyze")

    measure_numbers = sorted(
        {int(m.number) for m in stream.recurse().getElementsByClass("Measure") if m.number}
    )
    if not measure_numbers:
        measure_numbers = [1]
    first, last = measure_numbers[0], measure_numbers[-1]
    total_measures = last - first + 1

    # --- Priors from the MusicXML key signature ---
    # A piece can change signature mid-way (a real modulation with a key-change
    # mark in the score). Get the signature active at each measure; use the
    # initial signature as the home-key prior.
    measure_fifths = _fifths_per_measure(stream, first, last)
    initial_fifths = measure_fifths.get(first)
    declared_mode = _declared_mode(stream)
    home_candidates = _keys_for_fifths(initial_fifths)  # list of (name, pc, is_major)

    # --- Global correlation on the whole piece, constrained to the home signature ---
    global_hist = _pitch_histogram(stream, first, last)
    global_key, global_corr = _best_constrained_key(
        global_hist, home_candidates, declared_mode
    )

    if total_measures < window_measures:
        return (
            KeyRegion(
                start_measure=first,
                end_measure=last,
                key=global_key,
                confidence=global_corr,
            ),
        )

    # --- Windowed sweep ---
    # Each window: compute the best key under *its own* active signature (which
    # may be the home signature or a mid-piece signature change), plus the best
    # unconstrained key. An outside-signature key is only accepted when it
    # sustains across KEY_SWITCH_MIN_WINDOWS consecutive windows at margin.
    raw_windows: list[tuple[int, int, str, float, str, float]] = []
    for start in range(first, last - window_measures + 2):
        end = start + window_measures - 1
        hist = _pitch_histogram(stream, start, end)
        if hist.sum() == 0:
            continue
        # Active signature for this window = signature at its *start* measure
        # (signature changes take effect from the measure they're declared in).
        window_fifths = measure_fifths.get(start, initial_fifths)
        window_candidates = _keys_for_fifths(window_fifths)
        in_key, in_corr = _best_constrained_key(hist, window_candidates, declared_mode)
        out_key, out_corr = _best_unconstrained_key(hist)
        raw_windows.append((start, end, in_key, in_corr, out_key, out_corr))

    window_results: list[tuple[int, int, str, float]] = []
    for i, (start, end, in_key, in_corr, out_key, out_corr) in enumerate(raw_windows):
        margin = out_corr - in_corr
        same_key_streak = 0
        if margin >= KEY_SWITCH_MARGIN:
            for j in range(i, len(raw_windows)):
                _, _, _ij_in, ij_in_corr, ij_out, ij_out_corr = raw_windows[j]
                if ij_out == out_key and (ij_out_corr - ij_in_corr) >= KEY_SWITCH_MARGIN:
                    same_key_streak += 1
                else:
                    break

        if same_key_streak >= KEY_SWITCH_MIN_WINDOWS:
            window_results.append((start, end, out_key, out_corr))
        else:
            window_results.append((start, end, in_key, in_corr))

    if not window_results:
        return (
            KeyRegion(
                start_measure=first,
                end_measure=last,
                key=global_key,
                confidence=global_corr,
            ),
        )

    min_region = max(1, window_measures // 2)
    regions = _coalesce_windows(
        window_results, first, last, global_key, global_corr, min_region
    )
    return regions


# ---------------------------------------------------------------------------
# histogram + KS scoring


def _pitch_histogram(stream: object, start: int, end: int) -> np.ndarray:
    """Sum of quarter-length durations per pitch class in measures [start, end]."""
    w = stream.measures(start, end)  # type: ignore[attr-defined]
    if w is None:
        return np.zeros(12)
    hist = np.zeros(12)
    for n in w.flatten().notes:
        ql = float(n.quarterLength)
        for p in n.pitches:
            hist[p.pitchClass] += ql
    return hist


def _correlate(hist: np.ndarray, profile: np.ndarray, tonic_pc: int) -> float:
    prof = np.roll(profile, tonic_pc)
    h_mean = hist.mean()
    p_mean = prof.mean()
    num = float(((hist - h_mean) * (prof - p_mean)).sum())
    den = float(
        np.sqrt(((hist - h_mean) ** 2).sum() * ((prof - p_mean) ** 2).sum())
    )
    return num / den if den else 0.0


def _best_constrained_key(
    hist: np.ndarray,
    candidates: list[tuple[str, int, bool]],
    declared_mode: str | None,
) -> tuple[str, float]:
    """Score only the candidate keys; return the best."""
    if not candidates:
        return _best_unconstrained_key(hist)
    scored = []
    for name, pc, is_major in candidates:
        profile = _MAJOR_PROFILE if is_major else _MINOR_PROFILE
        c = _correlate(hist, profile, pc)
        # If MusicXML explicitly declared a mode, bias that strongly.
        if declared_mode == "major" and is_major:
            c += 0.05
        elif declared_mode == "minor" and not is_major:
            c += 0.05
        scored.append((name, c))
    scored.sort(key=lambda x: -x[1])
    return scored[0]


def _best_unconstrained_key(hist: np.ndarray) -> tuple[str, float]:
    best_name = ""
    best_corr = -2.0
    for pc in range(12):
        for is_major, profile in ((True, _MAJOR_PROFILE), (False, _MINOR_PROFILE)):
            c = _correlate(hist, profile, pc)
            if c > best_corr:
                best_corr = c
                best_name = _key_name(pc, is_major)
    return best_name, best_corr


def _key_name(pc: int, is_major: bool) -> str:
    # Use flat spellings when the tonic fits naturally on a flat side
    # (a,d,e,g,c minor are canonically flats; F major is a flat side).
    use_flats = (not is_major and pc in {9, 2, 7, 0, 5}) or (is_major and pc in {5})
    names = _PITCH_NAMES_FLAT if use_flats else _PITCH_NAMES_SHARP
    name = names[pc]
    return f"{name} major" if is_major else f"{name} minor"


# ---------------------------------------------------------------------------
# MusicXML priors


def _declared_fifths(stream: object) -> int | None:
    """Return the first <key><fifths> value encountered, or None if absent."""
    sigs = stream.flatten().getElementsByClass("KeySignature")  # type: ignore[attr-defined]
    if not sigs:
        return None
    return int(sigs[0].sharps)


def _fifths_per_measure(stream: object, first: int, last: int) -> dict[int, int]:
    """For each measure number, return the key-signature fifths active there.

    Handles mid-piece key changes by tracking the last KeySignature that
    appeared at or before each measure.
    """
    out: dict[int, int] = {}
    current_fifths: int | None = None
    for m in stream.recurse().getElementsByClass("Measure"):  # type: ignore[attr-defined]
        number = getattr(m, "number", None)
        if number is None:
            continue
        # Any KeySignature elements declared at the start of this measure update the current.
        for ks in m.getElementsByClass("KeySignature"):
            current_fifths = int(ks.sharps)
        if current_fifths is not None:
            out[int(number)] = current_fifths
    # Fill any gaps (measures without any KS declared) with the most recent signature.
    last_known: int | None = None
    for n in range(first, last + 1):
        if n in out:
            last_known = out[n]
        elif last_known is not None:
            out[n] = last_known
    return out


def _declared_mode(stream: object) -> str | None:
    """Return 'major' | 'minor' ONLY if the MusicXML <mode> element explicitly declared it.

    We intentionally do NOT fall through to `KeySignature.asKey().mode`: that
    always returns 'major' because music21 defaults to major when mode is
    unset, which would give us a false prior and bias detection for pieces
    whose engraved notation didn't declare mode (including our milonga).
    """
    sigs = stream.flatten().getElementsByClass("KeySignature")  # type: ignore[attr-defined]
    if not sigs:
        return None
    # Prefer the .mode attribute only if it's non-None; music21's KeySignature
    # sets mode=None when the MusicXML <mode> element is absent.
    mode = getattr(sigs[0], "mode", None)
    if mode in {"major", "minor"}:
        return mode
    return None


# fifths → (major tonic pc, minor tonic pc). The two parallel keys sharing this signature.
_FIFTHS_TO_TONICS: dict[int, tuple[int, int]] = {
    -7: (11, 8),   # Cb major / ab minor  (C-flat / A-flat minor)
    -6: (6, 3),    # Gb / eb
    -5: (1, 10),   # Db / bb
    -4: (8, 5),    # Ab / f
    -3: (3, 0),    # Eb / c
    -2: (10, 7),   # Bb / g
    -1: (5, 2),    # F / d
    0: (0, 9),     # C / a
    1: (7, 4),     # G / e
    2: (2, 11),    # D / b
    3: (9, 6),     # A / f#
    4: (4, 1),     # E / c#
    5: (11, 8),    # B / g#
    6: (6, 3),     # F# / d#
    7: (1, 10),    # C# / a#
}


def _keys_for_fifths(fifths: int | None) -> list[tuple[str, int, bool]]:
    """Two candidate keys (relative major + relative minor) for a given signature."""
    if fifths is None or fifths not in _FIFTHS_TO_TONICS:
        return []
    major_pc, minor_pc = _FIFTHS_TO_TONICS[fifths]
    return [
        (_key_name(major_pc, True), major_pc, True),
        (_key_name(minor_pc, False), minor_pc, False),
    ]


# ---------------------------------------------------------------------------
# Region coalescing (unchanged from prior version)


def _coalesce_windows(
    windows: list[tuple[int, int, str, float]],
    first: int,
    last: int,
    global_key: str,
    global_corr: float,
    min_region: int,
) -> tuple[KeyRegion, ...]:
    votes: dict[int, dict[str, list[float]]] = {m: {} for m in range(first, last + 1)}
    for start, end, key, corr in windows:
        for m in range(start, min(end, last) + 1):
            votes[m].setdefault(key, []).append(corr)

    measure_key: dict[int, tuple[str, float]] = {}
    for m in range(first, last + 1):
        tallies = votes.get(m) or {}
        if not tallies:
            measure_key[m] = (global_key, global_corr)
            continue
        best_key = max(tallies, key=lambda k: sum(tallies[k]))
        best_corrs = tallies[best_key]
        measure_key[m] = (best_key, sum(best_corrs) / len(best_corrs))

    regions: list[KeyRegion] = []
    run_key, run_corrs = measure_key[first][0], [measure_key[first][1]]
    run_start = first
    for m in range(first + 1, last + 1):
        k, c = measure_key[m]
        if k == run_key:
            run_corrs.append(c)
        else:
            regions.append(
                KeyRegion(
                    start_measure=run_start,
                    end_measure=m - 1,
                    key=run_key,
                    confidence=sum(run_corrs) / len(run_corrs),
                )
            )
            run_key, run_corrs, run_start = k, [c], m
    regions.append(
        KeyRegion(
            start_measure=run_start,
            end_measure=last,
            key=run_key,
            confidence=sum(run_corrs) / len(run_corrs),
        )
    )

    regions = _drop_short_regions(regions, min_region, global_key, global_corr)
    return tuple(regions)


def _drop_short_regions(
    regions: list[KeyRegion],
    min_region: int,
    global_key: str,
    global_corr: float,
) -> list[KeyRegion]:
    if len(regions) == 1:
        return regions

    changed = True
    while changed:
        changed = False
        for i, r in enumerate(regions):
            length = r.end_measure - r.start_measure + 1
            if length >= min_region:
                continue
            left = regions[i - 1] if i > 0 else None
            right = regions[i + 1] if i < len(regions) - 1 else None
            target = (
                left
                if left and (not right or (left.confidence or 0) >= (right.confidence or 0))
                else right
            )
            if target is None:
                break
            if target is left and left is not None:
                merged = KeyRegion(
                    start_measure=left.start_measure,
                    end_measure=r.end_measure,
                    key=left.key,
                    confidence=left.confidence,
                )
                regions = regions[: i - 1] + [merged] + regions[i + 1 :]
            elif target is right and right is not None:
                merged = KeyRegion(
                    start_measure=r.start_measure,
                    end_measure=right.end_measure,
                    key=right.key,
                    confidence=right.confidence,
                )
                regions = regions[:i] + [merged] + regions[i + 2 :]
            changed = True
            break

    if not regions:
        return [
            KeyRegion(
                start_measure=1,
                end_measure=1,
                key=global_key,
                confidence=global_corr,
            )
        ]
    return regions

"""Cadence detection over a RomanEvent stream.

See specs/feature-harmony-cadence.md.

We pattern-match within single key regions only — cross-key V→I is treated as
a modulation, not a cadence. Inversion detection is string-based on the numeral
figure (e.g. "V6" is inverted, "V7" is root-position 7th).
"""

from __future__ import annotations

import re

from agentic_sheet_music.types import Cadence, RomanEvent


def find_cadences(
    rn_events: tuple[RomanEvent, ...],
    *,
    min_phrase_length: int = 2,  # noqa: ARG001 — reserved; current rules use position only
) -> tuple[Cadence, ...]:
    """Return cadences discovered in the RN stream, in score order."""
    if not rn_events:
        return ()

    cadences: list[Cadence] = []
    authentic_measures: set[int] = set()  # end_measure of any PAC/IAC/DC — suppresses PC nearby

    # First pass: authentic, deceptive, plagal, phrygian (two-chord patterns).
    for i in range(len(rn_events) - 1):
        a, b = rn_events[i], rn_events[i + 1]
        if a.numeral == "?" or b.numeral == "?":
            continue
        if a.key != b.key:
            continue

        kind = _classify_pair(a, b)
        if kind in {"PAC", "IAC", "DC"}:
            cadences.append(
                Cadence(
                    kind=kind,
                    start_measure=a.measure,
                    end_measure=b.measure,
                    rationale=_rationale(kind, a, b),
                )
            )
            authentic_measures.add(b.measure)

    # Second pass: plagal and phrygian — only where no authentic cadence sits nearby.
    for i in range(len(rn_events) - 1):
        a, b = rn_events[i], rn_events[i + 1]
        if a.numeral == "?" or b.numeral == "?" or a.key != b.key:
            continue
        kind = _classify_pair(a, b)

        if kind == "PC":
            if _has_nearby_authentic(b.measure, authentic_measures):
                continue
            cadences.append(
                Cadence(
                    kind=kind,
                    start_measure=a.measure,
                    end_measure=b.measure,
                    rationale=_rationale(kind, a, b),
                )
            )

        elif kind == "PhC":
            # Don't emit PhC if the V continues to a tonic (that makes it a normal
            # authentic cadence, not a standalone half cadence flavor).
            if i + 2 < len(rn_events):
                after = rn_events[i + 2]
                if after.key == b.key and _is_tonic(after, b.key):
                    continue
            cadences.append(
                Cadence(
                    kind=kind,
                    start_measure=a.measure,
                    end_measure=b.measure,
                    rationale=_rationale(kind, a, b),
                )
            )

    # Third pass: half cadences — V followed by nothing, rest, or a clear phrase-gap.
    cadences.extend(_find_half_cadences(rn_events, authentic_measures))

    cadences.sort(key=lambda c: (c.start_measure, c.end_measure, c.kind))
    return tuple(cadences)


# ---------------------------------------------------------------------------


def _classify_pair(a: RomanEvent, b: RomanEvent) -> str | None:
    if _is_dominant(a) and _is_tonic(b, a.key):
        if _is_root_position(a) and _is_root_position(b):
            return "PAC"
        return "IAC"
    if _is_dominant(a) and _is_submediant(b, a.key):
        return "DC"
    if _is_subdominant(a) and _is_tonic(b, a.key):
        return "PC"
    if _is_minor_key(a.key) and _is_subdominant(a) and _is_dominant(b):
        return "PhC"
    return None


def _find_half_cadences(
    rn_events: tuple[RomanEvent, ...],
    authentic_measures: set[int],
) -> list[Cadence]:
    out: list[Cadence] = []
    n = len(rn_events)
    for i, ev in enumerate(rn_events):
        if ev.numeral == "?" or not _is_dominant(ev):
            continue

        is_last = i == n - 1
        next_in_same_key = (
            not is_last and rn_events[i + 1].key == ev.key and rn_events[i + 1].numeral != "?"
        )

        # HC if: it's the last event, or the next event is a clear new phrase
        # (tonic resumption) more than one measure away.
        emit = False
        if is_last:
            emit = True
        elif next_in_same_key:
            nxt = rn_events[i + 1]
            measure_gap = nxt.measure - ev.measure
            # A V that resolves directly to a tonic is handled by the authentic
            # cadence pass — skip here to avoid double-counting.
            if _is_tonic(nxt, ev.key) and measure_gap == 1:
                continue
            if _is_tonic(nxt, ev.key) and measure_gap >= 2:
                emit = True  # phrase breath, then tonic restart
        if not emit:
            continue
        if ev.measure in authentic_measures:
            continue  # this V is already the penultimate of an authentic cadence
        out.append(
            Cadence(
                kind="HC",
                start_measure=ev.measure,
                end_measure=ev.measure,
                rationale=f"half cadence on {ev.numeral} in {ev.key}",
            )
        )
    return out


def _has_nearby_authentic(measure: int, authentic: set[int]) -> bool:
    return any(abs(measure - m) <= 1 for m in authentic)


# ---------------------------------------------------------------------------
# RN-figure classifiers


_NUMERAL_STRIP_RE = re.compile(r"[^a-zA-Z°ø]")


def _bare_numeral(numeral: str) -> str:
    """Strip inversion digits and slash targets. 'V7/ii' -> 'V/ii', 'V6' -> 'V'."""
    # Split secondary-dominant slash first, preserve it as a marker.
    if "/" in numeral:
        left, right = numeral.split("/", 1)
        return f"{_strip_inversion(left)}/{right}"
    return _strip_inversion(numeral)


def _strip_inversion(n: str) -> str:
    # Keep letters and diminished/half-dim marks; drop digits except the leading 7
    # (which indicates a 7th quality, not inversion).
    letters = _NUMERAL_STRIP_RE.sub("", n)
    return letters


def _is_root_position(event: RomanEvent) -> bool:
    """No inversion digit present. 'V', 'V7', 'i' are root position; 'V6', 'V65', 'i6' are not."""
    numeral = event.numeral.split("/", 1)[0]  # ignore secondary-dominant target
    # Digits in the figure that aren't the "7" marker = inversion.
    digits = [c for c in numeral if c.isdigit()]
    if not digits:
        return True
    return digits == ["7"]


def _is_dominant(event: RomanEvent) -> bool:
    base = _bare_numeral(event.numeral)
    # V or V7 — but NOT V/X (that's a secondary dominant pointing elsewhere).
    return base in {"V", "V/"} or base.startswith("V") and "/" not in event.numeral and base.rstrip("°ø") in {"V", "V7"}


def _is_tonic(event: RomanEvent, key: str) -> bool:
    if event.key != key:
        return False
    base = _bare_numeral(event.numeral)
    return base in {"I", "i"}


def _is_submediant(event: RomanEvent, key: str) -> bool:
    if event.key != key:
        return False
    base = _bare_numeral(event.numeral)
    return base in {"vi", "VI"}


def _is_subdominant(event: RomanEvent) -> bool:
    base = _bare_numeral(event.numeral)
    return base in {"IV", "iv"}


def _is_minor_key(key: str) -> bool:
    return "minor" in key.lower()


# ---------------------------------------------------------------------------


def _rationale(kind: str, a: RomanEvent, b: RomanEvent) -> str:
    key = a.key
    base_map = {
        "PAC": "perfect authentic",
        "IAC": "imperfect authentic",
        "HC": "half",
        "DC": "deceptive",
        "PC": "plagal",
        "PhC": "Phrygian half",
    }
    name = base_map.get(kind, kind)
    return f"{name} cadence: {a.numeral} -> {b.numeral} in {key}"

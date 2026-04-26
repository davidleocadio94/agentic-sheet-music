"""Evaluator: per-measure exact-match between candidate and ground-truth MusicXML.

Metric:
  for each measure number M present in either file:
    - extract a normalised "measure signature" (pitches + rhythms + voices)
    - 1 if signatures match exactly, 0 otherwise
  score = sum(matches) / total measures

A score of 1.0 means every measure is transcribed identically to GT.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path


@dataclass(frozen=True)
class MeasureSignature:
    """Hashable, comparable representation of one measure's content.

    A signature is a tuple of (voice, beat_offset, pitch_or_rest, duration).
    Two measures with the same signature are considered identical for OMR
    correctness purposes. We deliberately drop layout, fingerings, dynamics,
    articulations — the eval is about pitches/rhythms/voices, not engraving.
    """
    events: tuple[tuple[int, str, str, str], ...]
    # (voice, beat_offset_str, pitch_or_rest, duration_str)


@dataclass(frozen=True)
class MeasureComparison:
    measure: int
    expected: MeasureSignature
    actual: MeasureSignature | None
    match: bool
    diff_summary: str = ""


@dataclass(frozen=True)
class EvalResult:
    fixture_name: str
    total_measures: int
    matched_measures: int
    measures: tuple[MeasureComparison, ...] = field(default_factory=tuple)

    @property
    def score(self) -> float:
        if self.total_measures == 0:
            return 0.0
        return self.matched_measures / self.total_measures

    @property
    def passed(self) -> bool:
        return self.matched_measures == self.total_measures and self.total_measures > 0


def evaluate(
    candidate_xml: Path,
    ground_truth_xml: Path,
    *,
    fixture_name: str | None = None,
) -> EvalResult:
    """Compare a candidate MusicXML to a ground-truth MusicXML."""
    name = fixture_name or candidate_xml.stem
    gt_measures = _measures(ground_truth_xml)
    cand_measures = _measures(candidate_xml)

    comps: list[MeasureComparison] = []
    matched = 0
    for m_num in sorted(set(gt_measures) | set(cand_measures)):
        gt_sig = gt_measures.get(m_num)
        cand_sig = cand_measures.get(m_num)
        if gt_sig is None:
            # Candidate has an extra measure GT doesn't.
            comps.append(
                MeasureComparison(
                    measure=m_num,
                    expected=MeasureSignature(events=()),
                    actual=cand_sig,
                    match=False,
                    diff_summary="extra measure not in ground truth",
                )
            )
            continue
        if cand_sig is None:
            comps.append(
                MeasureComparison(
                    measure=m_num,
                    expected=gt_sig,
                    actual=None,
                    match=False,
                    diff_summary="measure missing from candidate",
                )
            )
            continue
        if gt_sig == cand_sig:
            matched += 1
            comps.append(
                MeasureComparison(
                    measure=m_num,
                    expected=gt_sig,
                    actual=cand_sig,
                    match=True,
                )
            )
        else:
            comps.append(
                MeasureComparison(
                    measure=m_num,
                    expected=gt_sig,
                    actual=cand_sig,
                    match=False,
                    diff_summary=_diff(gt_sig, cand_sig),
                )
            )

    return EvalResult(
        fixture_name=name,
        total_measures=len(gt_measures),
        matched_measures=matched,
        measures=tuple(comps),
    )


# ---------------------------------------------------------------------------
# parsing


def _measures(xml_path: Path) -> dict[int, MeasureSignature]:
    """Extract a {measure_number: MeasureSignature} mapping from a MusicXML file."""
    root = ET.parse(xml_path).getroot()
    out: dict[int, MeasureSignature] = {}

    for part in root.findall("part"):
        # `<divisions>` declared in any measure stays in effect for subsequent
        # measures until another <divisions> appears (per MusicXML spec).
        divisions = Fraction(1)
        for m in part.findall("measure"):
            try:
                num = int(m.get("number", ""))
            except ValueError:
                continue
            sig, divisions = _measure_signature(m, divisions)
            if num in out:
                out[num] = MeasureSignature(events=out[num].events + sig.events)
            else:
                out[num] = sig
    return out


def _measure_signature(
    measure_el: ET.Element, divisions: Fraction
) -> tuple[MeasureSignature, Fraction]:
    """Build a normalised signature for one <measure>.

    Walks the measure's <note> elements in document order, tracking voice +
    cumulative beat offset (which resets per voice via <backup>). Returns
    the (possibly updated) divisions value so it persists to the next measure.
    """
    events: list[tuple[int, str, str, str]] = []
    voice_offsets: dict[int, Fraction] = {1: Fraction(0)}
    current_voice = 1

    for child in measure_el.iter():
        if child.tag == "divisions" and child.text:
            try:
                divisions = Fraction(int(child.text))
            except ValueError:
                pass
        elif child.tag == "backup":
            dur_el = child.find("duration")
            if dur_el is not None and dur_el.text:
                amt = Fraction(int(dur_el.text)) / divisions
                voice_offsets[current_voice] = voice_offsets.get(current_voice, Fraction(0)) - amt
        elif child.tag == "forward":
            dur_el = child.find("duration")
            if dur_el is not None and dur_el.text:
                amt = Fraction(int(dur_el.text)) / divisions
                voice_offsets[current_voice] = voice_offsets.get(current_voice, Fraction(0)) + amt
        elif child.tag == "note":
            voice_el = child.find("voice")
            if voice_el is not None and voice_el.text:
                try:
                    current_voice = int(voice_el.text)
                except ValueError:
                    current_voice = 1
            voice_offsets.setdefault(current_voice, Fraction(0))

            duration_el = child.find("duration")
            if duration_el is None or not duration_el.text:
                continue
            try:
                dur = Fraction(int(duration_el.text)) / divisions
            except ValueError:
                continue

            offset = voice_offsets[current_voice]

            if child.find("rest") is not None:
                token = "rest"
            else:
                pitch_el = child.find("pitch")
                if pitch_el is None:
                    continue
                step = pitch_el.findtext("step", default="?")
                octave = pitch_el.findtext("octave", default="?")
                alter = pitch_el.findtext("alter", default="0")
                token = f"{step}{_alter_str(alter)}{octave}"

            # Chord members share the previous note's offset — don't advance.
            is_chord = child.find("chord") is not None
            events.append(
                (
                    current_voice,
                    _frac_str(offset),
                    token,
                    _frac_str(dur),
                )
            )
            if not is_chord:
                voice_offsets[current_voice] = offset + dur

    return MeasureSignature(events=tuple(events)), divisions


def _alter_str(alter: str) -> str:
    if alter in {"", "0", None}:
        return ""
    if alter == "1":
        return "#"
    if alter == "-1":
        return "b"
    if alter == "2":
        return "##"
    if alter == "-2":
        return "bb"
    return f"({alter})"


def _frac_str(f: Fraction) -> str:
    if f.denominator == 1:
        return str(f.numerator)
    return f"{f.numerator}/{f.denominator}"


def _diff(expected: MeasureSignature, actual: MeasureSignature) -> str:
    exp_set = set(expected.events)
    act_set = set(actual.events)
    only_exp = sorted(exp_set - act_set)
    only_act = sorted(act_set - exp_set)
    parts = []
    if only_exp:
        parts.append("missing: " + ", ".join(f"v{v}@{o}:{p}({d})" for v, o, p, d in only_exp[:5]))
    if only_act:
        parts.append("extra: " + ", ".join(f"v{v}@{o}:{p}({d})" for v, o, p, d in only_act[:5]))
    return "; ".join(parts) or "ordering or count differs"

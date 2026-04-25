"""Ingest: path -> Score. See specs/feature-omr-ingest.md."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from xml.etree.ElementTree import ParseError

from music21 import converter
from music21.musicxml.xmlToM21 import MusicXMLImportException

logger = logging.getLogger(__name__)

from agentic_sheet_music.omr.pdf_to_musicxml import (
    AudiverisNotInstalled,
    OmrFailed,
    pdf_to_musicxml,
    pdf_to_musicxml_all,
    which_audiveris,
)
from agentic_sheet_music.types import Part, Score, ScoreMeta

OMR_CONFIDENCE = 0.7  # default trust level for OMR-derived scores

MUSICXML_EXTS = frozenset({".musicxml", ".xml", ".mxl"})
MIDI_EXTS = frozenset({".mid", ".midi"})
IMAGE_EXTS = frozenset({".pdf", ".png", ".jpg", ".jpeg"})
SUPPORTED = MUSICXML_EXTS | MIDI_EXTS | IMAGE_EXTS


class IngestError(Exception):
    """Base class for all ingest failures."""


class UnsupportedScoreFormat(IngestError):
    pass


class InvalidMusicXML(IngestError):
    pass


class OmrNotAvailable(IngestError):
    pass


class MidiIngestNotImplemented(IngestError):
    pass


def ingest(path: Path) -> Score:
    """Load a score file and return a validated Score.

    Raises:
        FileNotFoundError: path does not exist.
        UnsupportedScoreFormat: extension is not in SUPPORTED.
        InvalidMusicXML: MusicXML is present but unparseable.
        OmrNotAvailable: input is an image/PDF; OMR is not wired up in v1.
        MidiIngestNotImplemented: input is MIDI; not wired up in v1.
    """
    if not path.exists():
        raise FileNotFoundError(path)

    ext = path.suffix.lower()
    if ext in MUSICXML_EXTS:
        return _load_musicxml(path)
    if ext in MIDI_EXTS:
        raise MidiIngestNotImplemented(f"MIDI ingest not implemented in v1: {path}")
    if ext == ".pdf":
        return _load_pdf(path)
    if ext in IMAGE_EXTS:
        # PNG/JPG require a separate rasterization-free OMR backend; not wired yet.
        raise OmrNotAvailable(f"image OMR not available in v1 (PDF works): {path}")
    raise UnsupportedScoreFormat(f"{ext!r} not in supported extensions {sorted(SUPPORTED)}")


def _load_pdf(path: Path) -> Score:
    binary = which_audiveris()
    if binary is None:
        raise OmrNotAvailable(
            "Audiveris not installed. "
            "Install from https://github.com/Audiveris/audiveris/releases "
            "then retry."
        )
    try:
        musicxml = pdf_to_musicxml(path, audiveris_binary=binary)
    except AudiverisNotInstalled as e:
        raise OmrNotAvailable(str(e)) from e
    except OmrFailed as e:
        raise InvalidMusicXML(f"OMR failed on {path}: {e}") from e
    _repair_musicxml(musicxml)
    score = _load_musicxml(musicxml)
    return replace(score, source_confidence=OMR_CONFIDENCE)


# ---------------------------------------------------------------------------
# OMR-output repair: infer missing time signature from note durations.


_COMMON_METERS: tuple[tuple[str, Fraction], ...] = (
    # (display_string, quarter-length per measure).
    # Ordered so exact matches win before close-but-not-exact ones.
    ("2/4", Fraction(2)),
    ("3/4", Fraction(3)),
    ("4/4", Fraction(4)),
    ("6/8", Fraction(3)),      # 6/8 in compound is 3 beats × 2 = 6 eighths = 3 quarters
    ("9/8", Fraction(9, 2)),
    ("12/8", Fraction(6)),
    ("3/8", Fraction(3, 2)),
    ("2/2", Fraction(4)),      # cut time, 4 quarters
    ("5/4", Fraction(5)),
    ("7/8", Fraction(7, 2)),
)


def _repair_musicxml(path: Path) -> None:
    """Fix common OMR omissions in-place. Currently: missing <time> element.

    Audiveris sometimes doesn't detect the time signature on a score (happens
    on the Cardoso milonga — the 2/4 mark is a small graphic). Without a time
    signature music21 defaults every measure to 4/4, which silently mis-lays
    every barline in the piece. Inferring from note durations is more reliable
    than asking the user to re-OMR.
    """
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        logger.warning("could not re-parse MusicXML for repair: %s", e)
        return
    root = tree.getroot()

    if root.find(".//time") is not None:
        return  # Already has one.

    inferred = _infer_time_signature(root)
    if inferred is None:
        logger.warning(
            "OMR produced no time signature and inference failed for %s",
            path,
        )
        return

    beats, beat_type = inferred
    if not _inject_time_signature(root, beats, beat_type):
        logger.warning("couldn't inject time signature into %s", path)
        return

    tree.write(path, xml_declaration=True, encoding="utf-8")
    logger.info("injected inferred <time>%s/%s</time> into %s", beats, beat_type, path)


def _infer_time_signature(root: ET.Element) -> tuple[int, int] | None:
    """Look at the first few measures of the first part; pick the meter that
    matches best against the sum of note durations per measure.
    """
    # Find per-measure quarter-length totals using MusicXML <divisions> + <duration>.
    measure_quarter_lengths: list[Fraction] = []
    first_part = root.find("part")
    if first_part is None:
        return None
    divisions = Fraction(1)
    for m in first_part.findall("measure"):
        div_el = m.find(".//divisions")
        if div_el is not None and div_el.text:
            divisions = Fraction(int(div_el.text))
        total = Fraction(0)
        for note in m.findall("note"):
            if note.find("rest") is not None and note.find("chord") is None:
                dur_el = note.find("duration")
                if dur_el is not None and dur_el.text:
                    total += Fraction(int(dur_el.text)) / divisions
                continue
            if note.find("chord") is not None:
                continue  # chord tones share duration with the preceding note
            dur_el = note.find("duration")
            if dur_el is not None and dur_el.text:
                total += Fraction(int(dur_el.text)) / divisions
        if total > 0:
            measure_quarter_lengths.append(total)
        if len(measure_quarter_lengths) >= 10:
            break

    if not measure_quarter_lengths:
        return None

    # The true measure length is the MODE (most common value) of the per-measure
    # totals — pickup measures, tied notes crossing barlines, and short final
    # measures all pull the mean/median around, but the mode is stable.
    from collections import Counter

    counts = Counter(measure_quarter_lengths)
    measure_length, _ = counts.most_common(1)[0]

    # Match against common meters (exact), then fall back to (beats=int,
    # beat_type=4) by guessing from the raw length.
    for name, length in _COMMON_METERS:
        if length == measure_length:
            beats_str, beat_type_str = name.split("/")
            return int(beats_str), int(beat_type_str)

    # Fallback: assume denominator 4 and round the numerator.
    numerator = int(round(float(measure_length)))
    if 2 <= numerator <= 12:
        return numerator, 4
    return None


def _inject_time_signature(root: ET.Element, beats: int, beat_type: int) -> bool:
    """Find the first <measure> of each <part> and insert a <time> under its
    <attributes> element.
    """
    injected = False
    for part in root.findall("part"):
        first_measure = part.find("measure")
        if first_measure is None:
            continue
        attrs = first_measure.find("attributes")
        if attrs is None:
            attrs = ET.SubElement(first_measure, "attributes")
            first_measure.insert(0, attrs)
        time_el = ET.SubElement(attrs, "time")
        beats_el = ET.SubElement(time_el, "beats")
        beats_el.text = str(beats)
        beat_type_el = ET.SubElement(time_el, "beat-type")
        beat_type_el.text = str(beat_type)
        injected = True
    return injected


def ingest_all(path: Path) -> tuple[Score, ...]:
    """Ingest every movement of a multi-movement score.

    For PDFs, Audiveris splits at page gaps and returns one MusicXML per
    movement (e.g. guitar 1 + guitar 2 in a Cardoso milonga). For single-file
    MusicXML/MIDI inputs, returns a 1-tuple — there's only one movement.
    """
    if not path.exists():
        raise FileNotFoundError(path)

    ext = path.suffix.lower()
    if ext in MUSICXML_EXTS:
        return (_load_musicxml(path),)
    if ext in MIDI_EXTS:
        raise MidiIngestNotImplemented(f"MIDI ingest not implemented in v1: {path}")
    if ext == ".pdf":
        binary = which_audiveris()
        if binary is None:
            raise OmrNotAvailable(
                "Audiveris not installed. "
                "Install from https://github.com/Audiveris/audiveris/releases"
            )
        try:
            xmls = pdf_to_musicxml_all(path, audiveris_binary=binary)
        except AudiverisNotInstalled as e:
            raise OmrNotAvailable(str(e)) from e
        except OmrFailed as e:
            raise InvalidMusicXML(f"OMR failed on {path}: {e}") from e
        scores: list[Score] = []
        for xml in xmls:
            _repair_musicxml(xml)
            s = _load_musicxml(xml)
            scores.append(replace(s, source_confidence=OMR_CONFIDENCE))
        return tuple(scores)
    if ext in IMAGE_EXTS:
        raise OmrNotAvailable(f"image OMR not available in v1 (PDF works): {path}")
    raise UnsupportedScoreFormat(f"{ext!r} not in supported extensions {sorted(SUPPORTED)}")


def _load_musicxml(path: Path) -> Score:
    try:
        stream = converter.parse(str(path))
    except (MusicXMLImportException, ParseError, ValueError) as e:
        raise InvalidMusicXML(f"{path}: {e}") from e

    meta = _extract_meta(stream)
    parts = _extract_parts(stream)
    return Score(
        musicxml_path=path,
        meta=meta,
        parts=parts,
        source_confidence=1.0,
    )


def _extract_meta(stream: object) -> ScoreMeta:
    md = getattr(stream, "metadata", None)
    title = (getattr(md, "title", None) or getattr(md, "movementName", None) or "") if md else ""
    composer = getattr(md, "composer", None) if md else None

    time_sig = None
    ts_list = stream.flatten().getElementsByClass("TimeSignature")  # type: ignore[attr-defined]
    if ts_list:
        time_sig = ts_list[0].ratioString

    key_sig = None
    ks_list = stream.flatten().getElementsByClass("KeySignature")  # type: ignore[attr-defined]
    if ks_list:
        ks = ks_list[0]
        key_sig = ks.asKey().name if hasattr(ks, "asKey") else str(ks)

    return ScoreMeta(
        title=title,
        composer=composer,
        time_signature=time_sig,
        key_signature=key_sig,
    )


def _extract_parts(stream: object) -> tuple[Part, ...]:
    parts_iter = stream.parts  # type: ignore[attr-defined]
    out: list[Part] = []
    for p in parts_iter:
        name = p.partName or ""
        instrument_obj = p.getInstrument(returnDefault=False) if hasattr(p, "getInstrument") else None
        instrument = instrument_obj.instrumentName if instrument_obj else None
        out.append(Part(name=name, instrument=instrument))
    return tuple(out)

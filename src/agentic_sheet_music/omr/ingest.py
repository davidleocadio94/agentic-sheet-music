"""Ingest: path -> Score.

For PDFs we shell out to Gemini Vision (the only OMR engine in this project as
of 2026-04-25). For MusicXML we just load and validate. The notion of "movements"
is gone — Gemini returns one MusicXML for the whole PDF.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import replace
from pathlib import Path
from xml.etree.ElementTree import ParseError

from music21 import converter
from music21.musicxml.xmlToM21 import MusicXMLImportException

from agentic_sheet_music.omr.gemini_omr import (
    GeminiOmrError,
    GeminiOmrNotConfigured,
    pdf_to_musicxml,
)
from agentic_sheet_music.types import Part, Score, ScoreMeta

logger = logging.getLogger(__name__)

OMR_CONFIDENCE = 0.85  # Gemini's per-page confidence; per-piece varies.

MUSICXML_EXTS = frozenset({".musicxml", ".xml", ".mxl"})
MIDI_EXTS = frozenset({".mid", ".midi"})
IMAGE_EXTS = frozenset({".pdf", ".png", ".jpg", ".jpeg"})
SUPPORTED = MUSICXML_EXTS | MIDI_EXTS | IMAGE_EXTS


class IngestError(Exception):
    pass


class UnsupportedScoreFormat(IngestError):
    pass


class InvalidMusicXML(IngestError):
    pass


class OmrNotAvailable(IngestError):
    pass


class MidiIngestNotImplemented(IngestError):
    pass


def ingest(path: Path) -> Score:
    """Load a score file and return a validated Score."""
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
        raise OmrNotAvailable(f"image OMR not available in v1 (PDF works): {path}")
    raise UnsupportedScoreFormat(f"{ext!r} not in supported extensions {sorted(SUPPORTED)}")


def ingest_all(path: Path) -> tuple[Score, ...]:
    """Same as ingest() but returns a 1-tuple — kept for backward compat with
    callers that expected multi-movement Audiveris output. Gemini doesn't split
    by movement; everything comes back as one Score.
    """
    return (ingest(path),)


def _load_pdf(path: Path) -> Score:
    out_xml = Path(tempfile.mkdtemp(prefix="gemini-omr-")) / f"{path.stem}.musicxml"
    try:
        pdf_to_musicxml(path, out_xml)
    except GeminiOmrNotConfigured as e:
        raise OmrNotAvailable(str(e)) from e
    except GeminiOmrError as e:
        raise InvalidMusicXML(f"Gemini OMR failed on {path}: {e}") from e
    score = _load_musicxml(out_xml)
    return replace(score, source_confidence=OMR_CONFIDENCE)


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

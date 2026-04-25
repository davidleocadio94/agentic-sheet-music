"""Vision-LLM verifier for OMR output. See specs/feature-vlm-verify.md.

Audiveris OMR produces MusicXML with transcription errors on dense engravings.
Gemini 3.1 Pro is excellent at visual verification ("does this MusicXML match
what's on the page?") even when it's bad at primary transcription. We use it
as a verification layer only — never to auto-apply fixes.
"""

from __future__ import annotations

import io
import logging
import os
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Iterable

import pymupdf
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, Field

from agentic_sheet_music.types import (
    MeasureDisagreement,
    PageVerification,
    ScoreVerification,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.1-pro-preview"
DEFAULT_RENDER_DPI = 200
DEFAULT_MAX_OUTPUT_TOKENS = 32_000
DEFAULT_DOTENV_PATHS: tuple[Path, ...] = (
    Path.home() / "Documents/deeplearning_ai/slide-converter/.env",
)


class VerifierError(Exception):
    pass


class VerifierNotConfigured(VerifierError):
    pass


class VerifierNotAvailable(VerifierError):
    pass


# ---------------------------------------------------------------------------
# Pydantic schemas mirror the types.py dataclasses so Gemini can return
# structured JSON that maps cleanly to our types.


class _MeasureDisagreementPydantic(BaseModel):
    measure: int = Field(..., ge=1, description="Global measure number")
    issue: str = Field(..., description="What's wrong, in one sentence")
    suggested_fix: str | None = Field(
        None, description="Optional concrete fix (e.g. 'this should be a single C5, not a C5+Db5 chord')"
    )
    confidence: float = Field(..., ge=0.0, le=1.0)


class _PageVerificationPydantic(BaseModel):
    overall_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="How confident are you that the MusicXML matches the page?"
    )
    observed_key_signature: str | None = Field(
        None, description="What key signature is visible on the page (e.g. '1 flat', 'D minor', 'F major')?"
    )
    observed_time_signature: str | None = Field(
        None, description="What time signature is visible on the page (e.g. '2/4', '4/4')?"
    )
    disagreements: list[_MeasureDisagreementPydantic] = Field(
        default_factory=list,
        description="Specific measures where the MusicXML disagrees with the page image",
    )


# ---------------------------------------------------------------------------


def verify_score(
    *,
    source_pdf: Path,
    candidate_xml: Path,
    omr_book: Path,
    movement: int = 1,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    max_pages: int | None = None,
    render_dpi: int = DEFAULT_RENDER_DPI,
) -> ScoreVerification:
    """Verify a candidate MusicXML against the source PDF using a vision LLM."""
    key = _resolve_api_key(explicit=api_key)
    client = genai.Client(api_key=key)

    sheets = _sheets_for_movement(omr_book, movement)
    if not sheets:
        raise VerifierError(f"movement {movement} not found in {omr_book}")

    candidate_root = ET.parse(candidate_xml).getroot()
    per_page_measures = _measures_per_page(omr_book, movement)

    pages: list[PageVerification] = []
    with pymupdf.open(source_pdf) as doc:
        for i, sheet_number in enumerate(sheets):
            if max_pages is not None and i >= max_pages:
                break
            page_index = sheet_number - 1
            if page_index >= len(doc):
                continue
            measures_on_page = per_page_measures.get(sheet_number, [])
            if not measures_on_page:
                pages.append(
                    PageVerification(
                        page_index=page_index,
                        overall_confidence=0.0,
                        observed_key_signature=None,
                        observed_time_signature=None,
                        disagreements=(),
                    )
                )
                continue

            png_bytes = _render_page_png(doc[page_index], render_dpi)
            xml_slice = _slice_musicxml(candidate_root, measures_on_page)
            page_report = _verify_page(
                client=client,
                model=model,
                page_index=page_index,
                page_png=png_bytes,
                xml_slice=xml_slice,
                measure_range=(measures_on_page[0], measures_on_page[-1]),
            )
            pages.append(page_report)

    total = sum(len(p.disagreements) for p in pages)
    return ScoreVerification(pages=tuple(pages), model=model, total_disagreements=total)


# ---------------------------------------------------------------------------
# Per-page VLM call


def _verify_page(
    *,
    client: genai.Client,
    model: str,
    page_index: int,
    page_png: bytes,
    xml_slice: str,
    measure_range: tuple[int, int],
) -> PageVerification:
    start, end = measure_range
    prompt = (
        f"You are verifying an Optical Music Recognition result against the "
        f"original engraving.\n\n"
        f"This image is page {page_index + 1} of a classical guitar score. "
        f"The MusicXML below claims to transcribe measures {start}-{end} of "
        f"this page.\n\n"
        f"For each measure, compare the MusicXML to what you actually see on "
        f"the page. Report disagreements you can visually verify. Do NOT "
        f"invent fixes for things you can't read clearly — if the image is "
        f"unclear in a spot, lower your `overall_confidence` instead.\n\n"
        f"Common OMR errors to look for:\n"
        f"- Chord groupings: two notes combined into one chord when they're "
        f"actually in different voices or different onsets.\n"
        f"- Duplicated pitches (same note listed twice).\n"
        f"- Wrong pitches (accidentals flipped, octave off).\n"
        f"- Missing time signature or key signature changes.\n"
        f"- Merged barlines (one XML measure covering two visual measures).\n\n"
        f"Candidate MusicXML (only the relevant slice):\n"
        f"```xml\n{xml_slice}\n```\n\n"
        f"Return a structured report."
    )

    try:
        resp = client.models.generate_content(
            model=model,
            contents=[
                genai_types.Part.from_bytes(data=page_png, mime_type="image/png"),
                prompt,
            ],
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_PageVerificationPydantic,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            ),
        )
    except Exception as e:  # noqa: BLE001 — SDK raises a mix of error types
        logger.warning("Gemini call failed for page %d: %s", page_index, e)
        return PageVerification(
            page_index=page_index,
            overall_confidence=0.0,
            observed_key_signature=None,
            observed_time_signature=None,
            disagreements=(
                MeasureDisagreement(
                    measure=start,
                    issue=f"verifier call failed: {e}",
                    suggested_fix=None,
                    confidence=0.0,
                ),
            ),
        )

    if not getattr(resp, "text", None):
        logger.warning("Gemini returned empty response for page %d", page_index)
        return PageVerification(
            page_index=page_index,
            overall_confidence=0.0,
            observed_key_signature=None,
            observed_time_signature=None,
            disagreements=(),
        )

    try:
        parsed = _PageVerificationPydantic.model_validate_json(resp.text)
    except Exception as e:  # noqa: BLE001
        logger.warning("Gemini returned malformed JSON for page %d: %s", page_index, e)
        return PageVerification(
            page_index=page_index,
            overall_confidence=0.0,
            observed_key_signature=None,
            observed_time_signature=None,
            disagreements=(
                MeasureDisagreement(
                    measure=start,
                    issue=f"verifier JSON parse failed: {e}",
                    suggested_fix=None,
                    confidence=0.0,
                ),
            ),
        )

    return PageVerification(
        page_index=page_index,
        overall_confidence=parsed.overall_confidence,
        observed_key_signature=parsed.observed_key_signature,
        observed_time_signature=parsed.observed_time_signature,
        disagreements=tuple(
            MeasureDisagreement(
                measure=d.measure,
                issue=d.issue,
                suggested_fix=d.suggested_fix,
                confidence=d.confidence,
            )
            for d in parsed.disagreements
        ),
    )


# ---------------------------------------------------------------------------
# MusicXML slicing / .omr parsing


def _slice_musicxml(root: ET.Element, measure_numbers: Iterable[int]) -> str:
    """Return a minimal MusicXML document containing only the given measures."""
    wanted = set(measure_numbers)
    # Deep-copy a shallow version: part-list + a new <part> with only the
    # matching <measure> children.
    new_root = ET.Element(root.tag, attrib=root.attrib)
    for child in root:
        if child.tag == "part":
            new_part = ET.SubElement(new_root, "part", attrib=child.attrib)
            for m in child.findall("measure"):
                try:
                    num = int(m.get("number", ""))
                except ValueError:
                    continue
                if num in wanted:
                    new_part.append(m)
        else:
            new_root.append(child)
    buf = io.BytesIO()
    ET.ElementTree(new_root).write(buf, xml_declaration=True, encoding="utf-8")
    return buf.getvalue().decode("utf-8")


def _sheets_for_movement(omr_book: Path, movement: int) -> list[int]:
    """Ordered list of sheet numbers belonging to movement (1-based)."""
    with zipfile.ZipFile(omr_book) as zf:
        tree = ET.parse(zf.open("book.xml"))
    groups: list[list[int]] = []
    current: list[int] = []
    for sheet in tree.getroot().findall("sheet"):
        sn = int(sheet.get("number", "0"))
        page = sheet.find("page")
        is_start = page is not None and page.get("movement-start") == "true"
        if is_start and current:
            groups.append(current)
            current = []
        current.append(sn)
    if current:
        groups.append(current)
    if movement < 1 or movement > len(groups):
        return []
    return groups[movement - 1]


def _measures_per_page(omr_book: Path, movement: int) -> dict[int, list[int]]:
    """Map sheet number → list of global measure numbers on that page."""
    sheets = _sheets_for_movement(omr_book, movement)
    if not sheets:
        return {}
    out: dict[int, list[int]] = {}
    running = 0
    with zipfile.ZipFile(omr_book) as zf:
        for sn in sheets:
            sheet_xml = ET.parse(zf.open(f"sheet#{sn}/sheet#{sn}.xml")).getroot()
            count = 0
            for page in sheet_xml.iter("page"):
                mc = page.get("measure-count")
                if mc:
                    count += int(mc)
            if count == 0:
                # Fallback: count <stack> elements.
                count = sum(1 for _ in sheet_xml.iter("stack"))
            page_measures = list(range(running + 1, running + 1 + count))
            running += count
            out[sn] = page_measures
    return out


def _render_page_png(page, dpi: int) -> bytes:
    pix = page.get_pixmap(dpi=dpi)
    return pix.tobytes("png")


# ---------------------------------------------------------------------------
# API key resolution


def _resolve_api_key(
    *,
    explicit: str | None = None,
    dotenv_paths: Iterable[Path] = DEFAULT_DOTENV_PATHS,
) -> str:
    """Find the Gemini API key: explicit arg → env → .env files."""
    if explicit:
        return explicit
    env = os.environ.get("GEMINI_API_KEY")
    if env:
        return env
    for p in dotenv_paths:
        if not p.exists():
            continue
        try:
            for line in p.read_text().splitlines():
                if line.startswith("GEMINI_API_KEY="):
                    value = line.split("=", 1)[1].strip()
                    # Strip common quoting.
                    if value and value[0] in {'"', "'"} and value[-1] == value[0]:
                        value = value[1:-1]
                    if value:
                        return value
        except OSError as e:
            logger.warning("could not read %s: %s", p, e)
    raise VerifierNotConfigured(
        "No Gemini API key. Set GEMINI_API_KEY env var or put it in one of: "
        + ", ".join(str(p) for p in dotenv_paths)
    )

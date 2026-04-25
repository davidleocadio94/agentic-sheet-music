"""Annotate a PDF score with harmony analysis markup.

See specs/feature-annotated-pdf.md.

Pipeline:
  1. parse_measure_boxes() reads Audiveris's .omr book file, extracts
     per-measure bounding boxes in PDF points.
  2. annotate_pdf() opens the source PDF read-only, draws roman numerals,
     cadence brackets, and key-region banners, writes a new file.
"""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf

from agentic_sheet_music.types import (
    Cadence,
    HarmonyAnalysis,
    KeyRegion,
    RomanEvent,
)

logger = logging.getLogger(__name__)


class OmrBookParseError(Exception):
    pass


class AnnotationOutputError(Exception):
    pass


@dataclass(frozen=True)
class MeasureBox:
    measure: int
    page_index: int
    x0: float
    y0: float
    x1: float
    y1: float
    staff_bottom: float
    staff_top: float


# ---------------------------------------------------------------------------
# Coordinate extraction


def parse_measure_boxes(
    omr_book: Path,
    source_pdf: Path,
    movement: int = 1,
) -> dict[int, MeasureBox]:
    """Extract per-measure page coordinates from Audiveris's .omr book file.

    Returns a dict keyed by global (per-movement) measure number starting at 1.
    Coordinates are in PDF points, suitable for PyMuPDF drawing.
    """
    if not omr_book.exists():
        raise OmrBookParseError(f"{omr_book} does not exist")
    if not source_pdf.exists():
        raise OmrBookParseError(f"{source_pdf} does not exist")

    sheets_for_movement = _sheets_for_movement(omr_book, movement)
    if not sheets_for_movement:
        raise ValueError(f"movement {movement} not found in {omr_book}")

    # PDF page dimensions (in points) for scaling.
    with pymupdf.open(source_pdf) as doc:
        page_dims = [(p.rect.width, p.rect.height) for p in doc]

    boxes: dict[int, MeasureBox] = {}
    global_measure = 0

    with zipfile.ZipFile(omr_book) as zf:
        for sheet_number in sheets_for_movement:
            page_index = sheet_number - 1  # 1-indexed sheets → 0-indexed pages
            if page_index >= len(page_dims):
                logger.warning(
                    "sheet %d has no matching page in %s", sheet_number, source_pdf
                )
                continue
            pdf_w, pdf_h = page_dims[page_index]

            xml_name = f"sheet#{sheet_number}/sheet#{sheet_number}.xml"
            try:
                with zf.open(xml_name) as f:
                    tree = ET.parse(f)
            except KeyError as e:
                raise OmrBookParseError(
                    f"{omr_book}: missing {xml_name}"
                ) from e

            root = tree.getroot()
            picture = root.find("picture")
            if picture is None:
                raise OmrBookParseError(f"{xml_name}: no <picture>")
            pic_w = int(picture.get("width", "0"))
            pic_h = int(picture.get("height", "0"))
            if pic_w <= 0 or pic_h <= 0:
                raise OmrBookParseError(f"{xml_name}: invalid picture dimensions")

            sx = pdf_w / pic_w
            sy = pdf_h / pic_h

            for system in root.iter("system"):
                staff_top, staff_bot = _system_staff_y_range(system)
                if staff_top is None or staff_bot is None:
                    continue
                for stack in system.iter("stack"):
                    global_measure += 1
                    x0_px = float(stack.get("left", "0"))
                    x1_px = float(stack.get("right", "0"))
                    if x1_px <= x0_px:
                        continue
                    boxes[global_measure] = MeasureBox(
                        measure=global_measure,
                        page_index=page_index,
                        x0=x0_px * sx,
                        y0=staff_top * sy,
                        x1=x1_px * sx,
                        y1=staff_bot * sy,
                        staff_top=staff_top * sy,
                        staff_bottom=staff_bot * sy,
                    )
    return boxes


def _sheets_for_movement(omr_book: Path, movement: int) -> list[int]:
    """Return the ordered list of sheet numbers belonging to `movement`."""
    with zipfile.ZipFile(omr_book) as zf:
        try:
            with zf.open("book.xml") as f:
                tree = ET.parse(f)
        except KeyError as e:
            raise OmrBookParseError(f"{omr_book}: no book.xml") from e

    root = tree.getroot()
    grouped: list[list[int]] = []
    current: list[int] = []
    for sheet in root.findall("sheet"):
        sheet_num = int(sheet.get("number", "0"))
        # movement-start is declared on the first <page> within the sheet.
        page = sheet.find("page")
        is_start = page is not None and page.get("movement-start") == "true"
        if is_start and current:
            grouped.append(current)
            current = []
        current.append(sheet_num)
    if current:
        grouped.append(current)

    if movement < 1 or movement > len(grouped):
        return []
    return grouped[movement - 1]


def _system_staff_y_range(system_el: ET.Element) -> tuple[float | None, float | None]:
    """Compute the y-range that covers every staff line in the system."""
    ys: list[float] = []
    for line in system_el.iter("line"):
        for point in line.iter("point"):
            y = point.get("y")
            if y is not None:
                ys.append(float(y))
    if not ys:
        return None, None
    return min(ys), max(ys)


# ---------------------------------------------------------------------------
# PDF drawing


_KEY_COLORS = [
    (0.1, 0.4, 0.8),  # blue
    (0.6, 0.2, 0.6),  # purple
    (0.8, 0.4, 0.1),  # orange
    (0.1, 0.6, 0.3),  # green
    (0.7, 0.1, 0.3),  # crimson
    (0.2, 0.5, 0.6),  # teal
]
_CADENCE_COLOR = (0.85, 0.2, 0.2)
# Larger type than v1. Classical guitar engravings have ~1.5cm (~42pt) of white
# space between systems — use it. Labels land well below the staff (past any
# fingering circles, accent marks, and dynamics) and cadence/key banners land
# well above it (past any tempo marks, rehearsal letters, and articulations).
_RN_FONT_SIZE = 18
_CADENCE_FONT_SIZE = 14
_KEY_BANNER_FONT_SIZE = 15
# Points below staff bottom to place roman numerals. Classical guitar scores
# typically have string fingerings (circled numbers) 12–18pt below the staff;
# we want to sit below that in the system-to-system whitespace.
_RN_Y_OFFSET = 42
# Points above staff top for cadence brackets + labels.
_CADENCE_Y_OFFSET = 18
# Points above staff top for the key-region banner. Sits above the cadence.
_KEY_BANNER_Y_OFFSET = 38
_RN_FONT = "hebo"  # Helvetica-Bold — larger presence on engravings
_HEADER_FONT = "hebo"


def annotate_pdf(
    source_pdf: Path,
    omr_book: Path,
    analysis: HarmonyAnalysis,
    output_pdf: Path,
    *,
    movement: int = 1,
) -> Path:
    """Write an annotated copy of `source_pdf` for a single movement.

    Never overwrites the source; never overwrites an existing output file.
    For multi-movement scores, use `annotate_pdf_all_movements` to cover
    every movement in one pass.
    """
    return annotate_pdf_all_movements(
        source_pdf,
        omr_book,
        (analysis,),
        output_pdf,
        movements=(movement,),
    )


def annotate_pdf_all_movements(
    source_pdf: Path,
    omr_book: Path,
    analyses: tuple[HarmonyAnalysis, ...],
    output_pdf: Path,
    *,
    movements: tuple[int, ...] | None = None,
) -> Path:
    """Annotate every movement of a multi-movement PDF in one output file.

    `analyses[i]` is drawn on the page range for `movements[i]`. If
    `movements` is None, defaults to 1..len(analyses).
    """
    source_pdf = source_pdf.resolve()
    output_pdf = output_pdf.resolve()

    if source_pdf == output_pdf:
        raise AnnotationOutputError("output_pdf must differ from source_pdf")
    if output_pdf.exists():
        raise AnnotationOutputError(
            f"output_pdf already exists: {output_pdf}. "
            "Refusing to overwrite; choose a different path or delete first."
        )
    if not analyses:
        raise ValueError("at least one HarmonyAnalysis required")

    mvts = movements if movements is not None else tuple(range(1, len(analyses) + 1))
    if len(mvts) != len(analyses):
        raise ValueError("movements and analyses must have the same length")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    with pymupdf.open(source_pdf) as doc:
        for mvt, analysis in zip(mvts, analyses, strict=True):
            boxes = parse_measure_boxes(omr_book, source_pdf, movement=mvt)
            if not boxes:
                logger.warning("movement %d produced no measure boxes; skipping", mvt)
                continue
            key_color_map = _assign_key_colors(analysis.key_regions)
            _draw_key_region_banners(doc, analysis.key_regions, boxes, key_color_map)
            _draw_roman_numerals(doc, analysis.roman_numerals, boxes, key_color_map)
            _draw_cadences(doc, analysis.cadences, boxes)
        doc.save(output_pdf, garbage=1, deflate=True)

    return output_pdf


def _assign_key_colors(
    key_regions: tuple[KeyRegion, ...],
) -> dict[str, tuple[float, float, float]]:
    seen: dict[str, tuple[float, float, float]] = {}
    for r in key_regions:
        if r.key not in seen:
            seen[r.key] = _KEY_COLORS[len(seen) % len(_KEY_COLORS)]
    return seen


def _draw_roman_numerals(
    doc,
    rns: tuple[RomanEvent, ...],
    boxes: dict[int, MeasureBox],
    key_colors: dict[str, tuple[float, float, float]],
) -> None:
    # Only one label per measure (the first RN for that measure) — v1 keeps it clean.
    placed: set[int] = set()
    for rn in rns:
        if rn.measure in placed:
            continue
        box = boxes.get(rn.measure)
        if box is None:
            logger.debug("no box for measure %d; skipping RN annotation", rn.measure)
            continue
        placed.add(rn.measure)
        page = doc[box.page_index]
        x = (box.x0 + box.x1) / 2.0
        y = box.staff_bottom + _RN_Y_OFFSET
        color = key_colors.get(rn.key, (0.1, 0.1, 0.1))
        text = rn.numeral
        # Center the text under the measure.
        tw = pymupdf.get_text_length(text, fontname=_RN_FONT, fontsize=_RN_FONT_SIZE)
        page.insert_text(
            (x - tw / 2.0, y),
            text,
            fontname="helv",
            fontsize=_RN_FONT_SIZE,
            color=color,
        )


def _draw_cadences(
    doc,
    cadences: tuple[Cadence, ...],
    boxes: dict[int, MeasureBox],
) -> None:
    for cad in cadences:
        start = boxes.get(cad.start_measure)
        end = boxes.get(cad.end_measure)
        if start is None or end is None:
            continue
        if start.page_index != end.page_index:
            # Cross-page cadence — only mark the end measure for v1.
            end_only = boxes.get(cad.end_measure)
            if end_only:
                _draw_cadence_bracket_single(doc, end_only, cad.kind)
            continue
        _draw_cadence_bracket(doc, start, end, cad.kind)


def _draw_cadence_bracket(
    doc,
    start: MeasureBox,
    end: MeasureBox,
    kind: str,
) -> None:
    page = doc[start.page_index]
    y = start.staff_top - _CADENCE_Y_OFFSET
    # Bracket line + small downward ticks at the ends.
    page.draw_line(
        (start.x0, y), (end.x1, y), color=_CADENCE_COLOR, width=1.4
    )
    page.draw_line(
        (start.x0, y), (start.x0, y + 4.0), color=_CADENCE_COLOR, width=1.4
    )
    page.draw_line(
        (end.x1, y), (end.x1, y + 4.0), color=_CADENCE_COLOR, width=1.4
    )
    # Label centered above the bracket.
    cx = (start.x0 + end.x1) / 2.0
    tw = pymupdf.get_text_length(kind, fontname=_HEADER_FONT, fontsize=_CADENCE_FONT_SIZE)
    page.insert_text(
        (cx - tw / 2.0, y - 3.0),
        kind,
        fontname="helv",
        fontsize=_CADENCE_FONT_SIZE,
        color=_CADENCE_COLOR,
    )


def _draw_cadence_bracket_single(doc, box: MeasureBox, kind: str) -> None:
    page = doc[box.page_index]
    y = box.staff_top - _CADENCE_Y_OFFSET
    page.draw_line(
        (box.x0, y), (box.x1, y), color=_CADENCE_COLOR, width=1.4
    )
    cx = (box.x0 + box.x1) / 2.0
    tw = pymupdf.get_text_length(kind, fontname=_HEADER_FONT, fontsize=_CADENCE_FONT_SIZE)
    page.insert_text(
        (cx - tw / 2.0, y - 3.0),
        kind,
        fontname="helv",
        fontsize=_CADENCE_FONT_SIZE,
        color=_CADENCE_COLOR,
    )


def _draw_key_region_banners(
    doc,
    regions: tuple[KeyRegion, ...],
    boxes: dict[int, MeasureBox],
    key_colors: dict[str, tuple[float, float, float]],
) -> None:
    for region in regions:
        box = boxes.get(region.start_measure)
        if box is None:
            continue
        color = key_colors.get(region.key, (0.1, 0.1, 0.1))
        page = doc[box.page_index]
        # Colored banner at the top-left of the region's first measure.
        y_marker = box.staff_top - _KEY_BANNER_Y_OFFSET
        page.insert_text(
            (box.x0, y_marker),
            region.key,
            fontname=_HEADER_FONT,
            fontsize=_KEY_BANNER_FONT_SIZE,
            color=color,
        )

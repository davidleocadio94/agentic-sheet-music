"""PDF -> MusicXML via Gemini Vision.

This is the only OMR engine in the project. The previous Audiveris-based
hybrid was dropped on 2026-04-25 because Gemini does the structural
verification well but Audiveris's transcription quality created an
irreducible error floor we couldn't break through.

The strategy here is intentionally minimal at v1: render the PDF to per-page
PNGs, ask Gemini for each page's MusicXML, stitch the pages into a single
document. Real improvements (per-measure crops, OpenCV preprocessing, multi-pass
sampling, etc.) come through the autoresearch loop driven by `eval-fixtures/`.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pymupdf
from google import genai
from google.genai import types as genai_types

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.1-pro-preview"
DEFAULT_RENDER_DPI = 200
DEFAULT_MAX_OUTPUT_TOKENS = 64_000

# We re-use the same dotenv lookup as before for dev convenience.
DEFAULT_DOTENV_PATHS: tuple[Path, ...] = (
    Path.home() / "Documents/deeplearning_ai/slide-converter/.env",
)

_PAGE_PROMPT = """\
Transcribe this page of sheet music as MusicXML 4.0.

Output ONLY the contents of `<part-list>` and `<part>` for this page.
Do NOT include `<?xml ...?>`, `<!DOCTYPE>`, or `<score-partwise>` wrappers.
Use the exact tag names from the MusicXML 4.0 spec. Be conservative:
if you can't read something clearly, omit it rather than guess.

Specifically:
- Every `<note>` must have either a `<pitch>` (with `<step>`, `<octave>`, optional `<alter>`)
  OR a `<rest/>`.
- Every `<note>` must have a `<duration>` matching `<divisions>` (declared once at the start).
- Use a single `<divisions>` value for the whole page. 8 (eighth-note granularity)
  works for most engravings; raise to 12 for compound meter or to 24 for triplets.
- For two-voice writing on one staff, emit `<voice>1</voice>` for the upper
  voice and `<voice>2</voice>` for the lower; separate the voices with
  `<backup><duration>...</duration></backup>` between them.
- Circled fingering numbers above noteheads are NOT triplets — never emit
  `<time-modification>` for them. Only emit time-modification when an actual
  triplet bracket is drawn.
- For a shared-notehead two-voice case (one head, two stems), the rhythm
  belonging to each voice is determined by the stem direction.

Output format: just the raw MusicXML fragment, no markdown fences, no commentary.
"""


class GeminiOmrError(Exception):
    pass


class GeminiOmrNotConfigured(GeminiOmrError):
    pass


def pdf_to_musicxml(
    pdf: Path,
    output_xml: Path,
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    render_dpi: int = DEFAULT_RENDER_DPI,
) -> Path:
    """Convert a PDF to MusicXML using Gemini Vision.

    Returns the path to the written MusicXML file.
    """
    key = _resolve_api_key(api_key)
    client = genai.Client(api_key=key)

    output_xml.parent.mkdir(parents=True, exist_ok=True)
    page_xmls: list[str] = []

    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc):
            png = page.get_pixmap(dpi=render_dpi).tobytes("png")
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=[
                        genai_types.Part.from_bytes(data=png, mime_type="image/png"),
                        _PAGE_PROMPT,
                    ],
                    config=genai_types.GenerateContentConfig(
                        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                    ),
                )
            except Exception as e:  # noqa: BLE001
                raise GeminiOmrError(
                    f"Gemini call failed on page {i + 1} of {pdf}: {e}"
                ) from e

            text = getattr(resp, "text", None) or ""
            if not text.strip():
                raise GeminiOmrError(
                    f"Gemini returned empty response for page {i + 1} of {pdf}"
                )
            page_xmls.append(_strip_markdown_fences(text))

    full_xml = _stitch_pages(page_xmls)
    output_xml.write_text(full_xml, encoding="utf-8")
    return output_xml


# ---------------------------------------------------------------------------
# helpers


def _strip_markdown_fences(text: str) -> str:
    """Gemini sometimes wraps responses in ```xml ... ``` despite instructions."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _stitch_pages(page_xmls: list[str]) -> str:
    """Combine per-page MusicXML fragments into a single MusicXML document.

    Strategy: take the first page's <part-list> as the document's <part-list>
    (every page should have the same one for a single-instrument score), then
    concatenate all pages' <measure> elements into a single <part>.
    """
    if not page_xmls:
        raise GeminiOmrError("no pages produced")

    # Crude but works for this single-instrument case: pull <part-list> from
    # the first page, then collect every <measure>...</measure> across all.
    part_list_match = re.search(
        r"<part-list>.*?</part-list>", page_xmls[0], re.DOTALL
    )
    part_list = part_list_match.group(0) if part_list_match else (
        '<part-list><score-part id="P1"><part-name>Music</part-name></score-part></part-list>'
    )

    part_id_match = re.search(r'<score-part id="([^"]+)"', part_list)
    part_id = part_id_match.group(1) if part_id_match else "P1"

    measures: list[str] = []
    for page in page_xmls:
        for m in re.findall(r"<measure[^>]*>.*?</measure>", page, re.DOTALL):
            measures.append(m)

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<score-partwise version="4.0">\n'
        f"  {part_list}\n"
        f'  <part id="{part_id}">\n'
        + "\n".join(f"    {m}" for m in measures)
        + f"\n  </part>\n"
        "</score-partwise>\n"
    )
    return body


def _resolve_api_key(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("GEMINI_API_KEY")
    if env:
        return env
    for p in DEFAULT_DOTENV_PATHS:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    raise GeminiOmrNotConfigured(
        "No Gemini API key. Set GEMINI_API_KEY env var or put it in "
        + ", ".join(str(p) for p in DEFAULT_DOTENV_PATHS)
    )

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
DEFAULT_RENDER_DPI = 400
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

## Step 1: read the staff context FIRST, before any notes

Before transcribing notes, identify and emit these as `<attributes>` in the
first measure:

- **Clef**: look at the symbol at the start of the staff.
  - Treble clef (G clef on line 2): the line wrapping at the bottom of the
    spiral is line 2 from the bottom. That line is G4. The bottom line is E4,
    spaces from bottom are F4 A4 C5 E5, lines from bottom are E4 G4 B4 D5 F5.
  - Bass clef (F clef on line 4): the second line from the top is F3. Lines
    from bottom are G2 B2 D3 F3 A3, spaces are A2 C3 E3 G3.
  - Alto clef (C clef on middle line): middle line is C4.
- **Time signature**: look at the two stacked numbers right after the clef
  and key. The top is `<beats>`, bottom is `<beat-type>`.
- **Key signature**: count the sharps or flats *between* the clef and time
  signature. Sharps are `<fifths>+N</fifths>`. Flats are `<fifths>-N</fifths>`.
  - 0=C major/A minor, 1#=G/e, 2#=D/b, 3#=A/f#, 4#=E/c#, 5#=B/g#
  - 1b=F/d, 2b=Bb/g, 3b=Eb/c, 4b=Ab/f, 5b=Db/bb

## Step 2: octave numbering (DOUBLE-CHECK every note)

Middle C is C4. The C just above middle C is C5. Going up an octave adds 1.
On a treble clef:
- The note on the bottom line is **E4** (not E5 or E3).
- Middle C is one ledger line BELOW the bottom line of treble.
- The top line is **F5**.
- The first ledger line above is **A5**, then C6.

Read each note's vertical position carefully against the staff lines. A note
sitting on the third line from the bottom of treble is **B4**. A note on
the second line from the top of treble is **D5**. Octave errors are the most
common mistake — slow down and count line-by-line.

## Step 3: key signature application

The key signature applies to EVERY note of that letter name in EVERY octave
unless cancelled by a natural sign. In D major (2 sharps), every F is F#
and every C is C#, including bass-staff notes. Emit `<alter>1</alter>` for
sharps, `<alter>-1</alter>` for flats.

Notes notated with explicit accidentals override the key signature for the
rest of that measure.

## Step 4: rhythms

Use a single `<divisions>` value for the whole page. **Use `<divisions>8</divisions>`**
unless the score has 16th-note triplets (then 24) or strict whole-note motion (then 4).

With divisions=8, durations are:
- whole = 32, half = 16, dotted-half = 24
- quarter = 8, dotted-quarter = 12
- eighth = 4, dotted-eighth = 6
- sixteenth = 2

Each `<note>` must have a `<duration>` matching `<divisions>`. The total
duration in a measure must equal `(beats * 32 / beat_type)`.

## Step 5: voices and chords

- For two-voice writing on one staff, emit `<voice>1</voice>` for the upper
  voice and `<voice>2</voice>` for the lower; separate the voices with
  `<backup><duration>...</duration></backup>` between them.
- For chords (multiple notes struck simultaneously in one voice), the second
  and subsequent notes in the chord get `<chord/>` as the first child.

## Step 6: things NOT to misinterpret

- Circled fingering numbers above/below noteheads are NOT triplets — never
  emit `<time-modification>` for them.
- Stem direction alone doesn't change pitch — read the notehead position.
- Letters like "p, i, m, a" near the staff are right-hand fingerings, not pitches.

## Output format

Just the raw MusicXML fragment, no markdown fences, no commentary.
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
            png = _render_page_cropped(page, render_dpi)
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


def _render_page_cropped(page, dpi: int) -> bytes:
    """Render a PDF page to PNG, cropped to its non-white content bounding box.

    Verovio output has a lot of whitespace (one staff at the top of a US-Letter
    page). Cropping reduces the image size Gemini sees and effectively zooms in
    on the music — pixels per notehead go up, vertical localisation improves.
    """
    pix = page.get_pixmap(dpi=dpi)
    w, h = pix.width, pix.height
    # PyMuPDF Pixmap supports per-byte access via .samples (RGB or RGBA).
    samples = pix.samples
    n = pix.n  # bytes per pixel
    # Find tight bounding box of non-white pixels.
    min_x, min_y, max_x, max_y = w, h, 0, 0
    threshold = 240  # treat near-white as background
    # Scan in strides to keep this fast (every ~3rd pixel).
    stride = 3
    for y in range(0, h, stride):
        row_start = y * w * n
        for x in range(0, w, stride):
            idx = row_start + x * n
            # Use luminance-ish sum of first 3 channels.
            if n >= 3:
                lum = (samples[idx] + samples[idx + 1] + samples[idx + 2]) // 3
            else:
                lum = samples[idx]
            if lum < threshold:
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y

    if min_x >= max_x or min_y >= max_y:
        # Empty page — fall back to full image.
        return pix.tobytes("png")

    # Pad the box a little so we don't clip stems / barlines.
    pad = max(20, dpi // 10)
    x0 = max(0, min_x - pad)
    y0 = max(0, min_y - pad)
    x1 = min(w, max_x + pad)
    y1 = min(h, max_y + pad)

    # Crop via PyMuPDF: re-render the same page using a clip rect in PDF space.
    # Convert the pixel bbox back to PDF points.
    scale = 72.0 / dpi
    clip = pymupdf.Rect(x0 * scale, y0 * scale, x1 * scale, y1 * scale)
    cropped = page.get_pixmap(dpi=dpi, clip=clip)
    return cropped.tobytes("png")


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

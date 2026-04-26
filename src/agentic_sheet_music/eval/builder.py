"""Ground-truth builder: MusicXML -> PDF via Verovio.

This is how the eval dataset stays trustworthy. We hand-write a small MusicXML,
then engrave it with Verovio. The PDF the OMR sees is GENERATED FROM the
ground truth, so by construction the GT and the source PDF are identical
information. No human-transcription error in the loop.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

VEROVIO_BIN = shutil.which("verovio") or "/opt/homebrew/bin/verovio"

# cairo lives under /opt/homebrew/lib on Apple Silicon brew installs;
# cairosvg won't find it without this on the dyld search path.
_CAIRO_ENV = {**os.environ, "DYLD_FALLBACK_LIBRARY_PATH": "/opt/homebrew/lib"}


class GroundTruthBuildError(Exception):
    pass


def musicxml_to_pdf(
    musicxml: Path,
    pdf_out: Path,
    *,
    verovio_bin: str = VEROVIO_BIN,
) -> Path:
    """Render a MusicXML file to PDF.

    Pipeline: verovio -> SVG -> cairosvg -> PDF.
    """
    if not musicxml.exists():
        raise GroundTruthBuildError(f"musicxml not found: {musicxml}")

    pdf_out.parent.mkdir(parents=True, exist_ok=True)
    svg_tmp = pdf_out.with_suffix(".svg")

    try:
        subprocess.run(
            [
                verovio_bin,
                "--scale", "200",
                "--adjust-page-height",
                "--adjust-page-width",
                "--page-margin-top", "50",
                "--page-margin-bottom", "50",
                "--page-margin-left", "50",
                "--page-margin-right", "50",
                str(musicxml),
                "-o", str(svg_tmp),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise GroundTruthBuildError(
            f"verovio failed on {musicxml}: {e}"
        ) from e
    except FileNotFoundError as e:
        raise GroundTruthBuildError(
            f"verovio not on PATH ({verovio_bin}). brew install verovio."
        ) from e

    try:
        # Import lazily so the package imports without cairo when unused.
        os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")
        import cairosvg  # noqa: PLC0415

        cairosvg.svg2pdf(url=str(svg_tmp), write_to=str(pdf_out))
    except Exception as e:  # noqa: BLE001
        raise GroundTruthBuildError(f"SVG->PDF conversion failed: {e}") from e
    finally:
        if svg_tmp.exists():
            svg_tmp.unlink()

    return pdf_out

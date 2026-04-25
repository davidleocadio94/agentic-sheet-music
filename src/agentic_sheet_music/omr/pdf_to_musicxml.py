"""PDF -> MusicXML via Audiveris. See specs/feature-omr-pdf.md."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIVERIS_DEFAULT = Path("/Applications/Audiveris.app/Contents/MacOS/Audiveris")


class OmrFailed(Exception):
    """Base class for all OMR errors."""


class AudiverisNotInstalled(OmrFailed):
    pass


class OmrTimeout(OmrFailed):
    pass


class OmrEmpty(OmrFailed):
    pass


def pdf_to_musicxml(
    pdf: Path,
    *,
    output_dir: Path | None = None,
    timeout_seconds: int = 600,
    audiveris_binary: Path = AUDIVERIS_DEFAULT,
) -> Path:
    """Convert a PDF to MusicXML via Audiveris (first movement only).

    Returns the path to the first movement's plain-XML file. For scores with
    multiple movements (Audiveris splits by page-gap and decorates `book.xml`
    with `movement-start="true"`), use `pdf_to_musicxml_all` to get every
    movement.
    """
    xmls = pdf_to_musicxml_all(
        pdf,
        output_dir=output_dir,
        timeout_seconds=timeout_seconds,
        audiveris_binary=audiveris_binary,
    )
    if len(xmls) > 1:
        logger.warning(
            "Audiveris split %s into %d movements; using %s, ignoring %s",
            pdf.name,
            len(xmls),
            xmls[0].name,
            [m.name for m in xmls[1:]],
        )
    return xmls[0]


def pdf_to_musicxml_all(
    pdf: Path,
    *,
    output_dir: Path | None = None,
    timeout_seconds: int = 600,
    audiveris_binary: Path = AUDIVERIS_DEFAULT,
) -> list[Path]:
    """Convert a PDF to MusicXML via Audiveris, returning one XML per movement.

    Audiveris produces one `.mxl` per movement (e.g. `milonga.mvt1.mxl`,
    `milonga.mvt2.mxl`). Returned paths are decompressed plain-XML files, in
    movement order.
    """
    if not audiveris_binary.exists():
        raise AudiverisNotInstalled(
            f"Audiveris not found at {audiveris_binary}. "
            "Install from https://github.com/Audiveris/audiveris/releases"
        )
    if not pdf.exists():
        raise FileNotFoundError(pdf)

    out = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="audiveris-"))
    out.mkdir(parents=True, exist_ok=True)

    cmd = [str(audiveris_binary), "-batch", "-export", "-output", str(out), str(pdf)]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as e:
        raise OmrTimeout(f"Audiveris exceeded {timeout_seconds}s on {pdf}") from e

    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.splitlines()[-20:])
        raise OmrFailed(f"Audiveris exited {result.returncode}:\n{stderr_tail}")

    mxls = sorted(out.glob("*.mxl"))
    if not mxls:
        raise OmrEmpty(f"Audiveris produced no .mxl output in {out}")

    return [_decompress_mxl(m) for m in mxls]


def _decompress_mxl(mxl: Path) -> Path:
    """Extract the MusicXML payload from a .mxl (zipped) file."""
    target_dir = mxl.parent / f"{mxl.stem}-extracted"
    target_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(mxl) as zf:
        # MXL archives have a META-INF/container.xml pointing to the rootfile,
        # but in practice Audiveris always emits a single top-level .xml file
        # with the same stem as the .mxl.
        candidates = [n for n in zf.namelist() if n.endswith(".xml") and not n.startswith("META-INF")]
        if not candidates:
            raise OmrFailed(f"{mxl} contains no MusicXML payload")
        zf.extractall(target_dir)
    extracted = target_dir / candidates[0]
    if not extracted.exists():
        # Fallback — some zips nest paths
        matches = list(target_dir.rglob(Path(candidates[0]).name))
        if not matches:
            raise OmrFailed(f"extracted payload missing from {target_dir}")
        extracted = matches[0]
    return extracted


def which_audiveris() -> Path | None:
    """Locate Audiveris, falling back to PATH. Returns None if not found."""
    if AUDIVERIS_DEFAULT.exists():
        return AUDIVERIS_DEFAULT
    found = shutil.which("Audiveris") or shutil.which("audiveris")
    return Path(found) if found else None

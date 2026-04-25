"""Tests for omr-pdf. Spec: specs/feature-omr-pdf.md.

Tests marked @pytest.mark.omr_binary require Audiveris to be installed. Run them with:
    uv run pytest -m omr_binary
"""

from pathlib import Path

import pytest

from agentic_sheet_music.omr.ingest import ingest
from agentic_sheet_music.omr.pdf_to_musicxml import (
    AudiverisNotInstalled,
    pdf_to_musicxml,
    which_audiveris,
)

MILONGA_PDF = Path("/Users/davidvillarreal/Documents/Music/ClassicalGuitar/milonga.pdf")


def test_omr_binary_missing_raises(tmp_path: Path) -> None:
    f = tmp_path / "fake.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(AudiverisNotInstalled):
        pdf_to_musicxml(f, audiveris_binary=tmp_path / "nonexistent")


@pytest.mark.omr_binary
def test_pdf_converts_to_score_via_audiveris(tmp_path: Path) -> None:
    if which_audiveris() is None:
        pytest.skip("Audiveris not installed")
    if not MILONGA_PDF.exists():
        pytest.skip(f"sample PDF missing at {MILONGA_PDF}")

    xml = pdf_to_musicxml(MILONGA_PDF, output_dir=tmp_path)
    assert xml.exists()
    assert xml.suffix == ".xml"
    content = xml.read_text()
    assert "<note" in content, "expected at least one <note> in MusicXML output"


@pytest.mark.omr_binary
def test_ingest_pdf_dispatches_to_omr() -> None:
    if which_audiveris() is None:
        pytest.skip("Audiveris not installed")
    if not MILONGA_PDF.exists():
        pytest.skip(f"sample PDF missing at {MILONGA_PDF}")

    score = ingest(MILONGA_PDF)
    assert score.source_confidence == 0.7  # OMR default
    assert len(score.parts) >= 1

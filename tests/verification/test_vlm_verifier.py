"""Tests for vlm-verify. Spec: specs/feature-vlm-verify.md."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

MILONGA_PDF = Path("/Users/davidvillarreal/Documents/Music/ClassicalGuitar/milonga.pdf")
MILONGA_OMR = Path("/tmp/audiveris-smoke/milonga.omr")


def test_key_resolution_prefers_explicit_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentic_sheet_music.omr.vlm_verifier import _resolve_api_key

    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    key = _resolve_api_key(explicit="from-arg", dotenv_paths=())
    assert key == "from-arg"


def test_key_resolution_env_wins_over_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agentic_sheet_music.omr.vlm_verifier import _resolve_api_key

    dotenv = tmp_path / ".env"
    dotenv.write_text("GEMINI_API_KEY=from-dotenv\n")
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")

    key = _resolve_api_key(explicit=None, dotenv_paths=(dotenv,))
    assert key == "from-env"


def test_key_resolution_falls_back_to_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agentic_sheet_music.omr.vlm_verifier import _resolve_api_key

    dotenv = tmp_path / ".env"
    dotenv.write_text('GEMINI_API_KEY="from-dotenv"\n')
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    key = _resolve_api_key(explicit=None, dotenv_paths=(dotenv,))
    assert key == "from-dotenv"


def test_key_resolution_raises_when_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agentic_sheet_music.omr.vlm_verifier import (
        VerifierNotConfigured,
        _resolve_api_key,
    )

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(VerifierNotConfigured):
        _resolve_api_key(explicit=None, dotenv_paths=(tmp_path / "nonexistent.env",))


@pytest.mark.omr_binary
def test_verifier_flags_milonga_issues() -> None:
    """Correctness: Gemini must flag at least one disagreement on page 1 of
    the milonga. Audiveris produced physically implausible chord groupings
    (e.g. B-4 + C#5 in m.3); a functioning verifier will notice.
    """
    from agentic_sheet_music.omr.pdf_to_musicxml import which_audiveris
    from agentic_sheet_music.omr.vlm_verifier import (
        VerifierNotConfigured,
        verify_score,
    )

    if which_audiveris() is None or not MILONGA_PDF.exists() or not MILONGA_OMR.exists():
        pytest.skip("Audiveris / milonga fixtures not available")

    # Need the candidate XML (already produced in earlier smoke runs).
    candidate = Path("/tmp/audiveris-smoke/extracted/milonga.mvt1.xml")
    if not candidate.exists():
        pytest.skip("candidate XML not available")

    try:
        report = verify_score(
            source_pdf=MILONGA_PDF,
            candidate_xml=candidate,
            omr_book=MILONGA_OMR,
            movement=1,
            max_pages=1,  # just page 1 for the test
        )
    except VerifierNotConfigured:
        pytest.skip("GEMINI_API_KEY not configured")

    assert len(report.pages) == 1
    page = report.pages[0]
    # Either the overall-confidence is low, or there are concrete disagreements.
    # Both mean "Gemini noticed something is off with this transcription."
    assert (
        len(page.disagreements) >= 1 or page.overall_confidence < 0.8
    ), f"Gemini saw no issues on a demonstrably imperfect page: {page}"

    # The observed key/time signatures should match reality: 1 flat
    # (F major / D minor) and 2/4.
    if page.observed_key_signature:
        assert "1" in page.observed_key_signature or "flat" in page.observed_key_signature.lower() or "d minor" in page.observed_key_signature.lower() or "f major" in page.observed_key_signature.lower(), (
            f"observed key looks wrong: {page.observed_key_signature!r}"
        )
    if page.observed_time_signature:
        assert page.observed_time_signature in {"2/4", "4/4"}, (
            f"expected 2/4 (visual) or 4/4 (Audiveris internal), got {page.observed_time_signature!r}"
        )

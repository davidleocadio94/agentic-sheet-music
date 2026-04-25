from pathlib import Path

import pytest

from agentic_sheet_music.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_missing_file_returns_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([str(tmp_path / "does-not-exist.musicxml")])
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err


def test_cli_ingests_musicxml_and_prints_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main([str(FIXTURES / "omr-ingest" / "c-major-scale.musicxml")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "C Major Scale" in out
    assert "parts: 1" in out
    assert "source_confidence: 1.00" in out


def test_cli_reports_ingest_error_on_unsupported_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("not a score")
    rc = main([str(f)])
    assert rc == 1
    assert "UnsupportedScoreFormat" in capsys.readouterr().err

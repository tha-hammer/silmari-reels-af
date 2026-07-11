"""Browser contract checks for failure/result display behavior."""

from __future__ import annotations

from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parents[2] / "web" / "index.html"


def test_succeeded_error_payload_is_not_finished_as_downloadable_result():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'const errorMessage = resultErrorMessage(j.result) || resultErrorMessage(j);' in html
    assert "if (errorMessage) throw new Error(`execution failed: ${errorMessage}`);" in html
    assert "function resultErrorMessage(result)" in html
    assert 'const resultError = resultErrorMessage(result);' in html
    assert "if (resultError) throw new Error(`execution failed: ${resultError}`);" in html

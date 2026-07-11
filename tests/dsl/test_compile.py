"""B8: Compile N segments to CompileResult(plan=FootageReel).

Tests the full compile_composite pipeline against the v1 fixture.
"""

from __future__ import annotations

from pathlib import Path

from reel_af.dsl.compile import compile_composite, load_words
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import (
    FootageReel,
    SourceRef,
    validate_renderable,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE_URL = "https://example.com/source.mp4"


def test_v1_fixture_compiles_to_renderable_reel():
    text = (FIXTURES / "v1_supported.ts.md").read_text()
    doc = read_composite(text, source_path=FIXTURES / "v1_supported.ts.md")
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "ok", f"expected ok, got {res.status}: {res.diagnostics}"
    assert res.plan is not None
    FootageReel.model_validate(res.plan.model_dump())
    validate_renderable(res.plan)


def test_v1_fixture_has_expected_segment_types():
    text = (FIXTURES / "v1_supported.ts.md").read_text()
    doc = read_composite(text, source_path=FIXTURES / "v1_supported.ts.md")
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.plan is not None
    kinds = [s.kind for s in res.plan.segments]
    assert "source" in kinds
    assert "black" in kinds


def test_v1_fixture_has_correct_transition_count():
    text = (FIXTURES / "v1_supported.ts.md").read_text()
    doc = read_composite(text, source_path=FIXTURES / "v1_supported.ts.md")
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.plan is not None
    assert len(res.plan.transitions) == len(res.plan.segments) - 1


def test_empty_composite_returns_error():
    doc = read_composite("")
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "error"
    assert res.plan is None
    codes = [d.code for d in res.diagnostics]
    assert "EMPTY_COMPOSITE" in codes


def test_unmatched_segment_returns_error():
    text = "00:00:04.120  This text does not appear anywhere in the word timings at all whatsoever xyzzy\n"
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "error"
    assert res.plan is None
    codes = [d.code for d in res.diagnostics]
    assert "UNMATCHED_SEGMENT" in codes

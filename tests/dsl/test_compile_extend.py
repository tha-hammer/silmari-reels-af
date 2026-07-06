"""B9: extend adjusts segment edges in the reel."""

from __future__ import annotations

from pathlib import Path

import pytest

from reel_af.dsl.compile import compile_composite, load_words
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import SourceRef, SourceSegment

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE_URL = "https://example.com/source.mp4"


def test_extend_tail_grows_end():
    text = "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning. [extend tail 0.4]\n"
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "ok"
    assert res.plan is not None
    seg = res.plan.segments[0]
    assert isinstance(seg, SourceSegment)
    assert seg.end_s > 7.9


def test_extend_head_moves_start():
    text = "00:01:12.300  So the fix isn't a smarter model. It's a tighter loop. [extend head 0.5]\n"
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "ok"
    assert res.plan is not None
    seg = res.plan.segments[0]
    assert isinstance(seg, SourceSegment)
    assert seg.start_s < 72.3


def test_extend_preserves_positive_duration():
    text = "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning. [extend tail 0.4]\n"
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.plan is not None
    seg = res.plan.segments[0]
    assert isinstance(seg, SourceSegment)
    assert seg.start_s < seg.end_s

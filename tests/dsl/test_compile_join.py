"""B10: join merges adjacent source segments with guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from reel_af.dsl.compile import compile_composite, load_words
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import SourceRef, SourceSegment

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE_URL = "https://example.com/source.mp4"


def test_join_merges_adjacent_same_source():
    text = (
        "00:01:12.300  So the fix isn't a smarter model. It's a tighter loop.\n"
        "\n"
        "[join]\n"
        "\n"
        "00:01:19.050  A loop you can actually see closing.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "ok"
    assert res.plan is not None
    assert len(res.plan.segments) == 1
    seg = res.plan.segments[0]
    assert isinstance(seg, SourceSegment)
    assert seg.start_s <= 72.3
    assert seg.end_s >= 81.16


def test_join_normal_within_gap_limit():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[join]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "ok"
    assert res.plan is not None
    assert len(res.plan.segments) == 1
